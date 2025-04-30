# FieldForge/drawing.py

"""
Handles custom GPU drawing for FieldForge objects in the 3D Viewport.
Includes the main draw callback, helper for offsetting vertices, redraw tagging,
and double-buffered line data for picking.
"""

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
import math
from mathutils import Vector, Matrix # Matrix might not be needed here, but Vector is

# Use relative imports assuming this file is in FieldForge/
# These might need adjustment based on your final project structure
try:
    from . import constants
    from . import utils # For find_parent_bounds, get_bounds_setting, is_sdf_source, geometry helpers
except ImportError:
    # Fallback for running script directly or if structure changes
    print("FieldForge WARN (drawing.py): Could not perform relative imports. Using placeholders.")
    # If running as standalone, these would need to be actual modules
    import constants
    import utils


# --- Module State ---
# Store the draw handle within this module
_draw_handle = None
# Double Buffering for picking data to avoid race conditions with event handlers
_draw_line_data_read = {}  # Data for event handlers to read (stable from previous frame)
_draw_line_data_write = {} # Data for draw callback to write to (current frame)

# --- Drawing Helpers ---

def offset_vertices(vertices, region_data: bpy.types.RegionView3D, camera_loc: Vector, offset_factor: float) -> list:
    """
    Offsets vertices slightly to mitigate depth fighting.
    - In Perspective view: Offsets towards the camera location.
    - In Orthographic view: Offsets along the constant view direction.
    Requires region_data (region_3d) to determine projection type.
    Returns a list of Vectors or original vertices if input is empty/invalid.
    """
    offset_verts = []
    if not vertices or not region_data:
        return vertices

    is_persp = getattr(region_data, 'is_perspective', True)
    view_direction_ortho = None
    if not is_persp:
        try:
            view_direction_ortho = -region_data.view_matrix.col[2].xyz.normalized()
        except (AttributeError, TypeError, ValueError, Exception) as e:
             view_direction_ortho = None

    for v in vertices:
        v_vec = Vector(v) if not isinstance(v, Vector) else v.copy()
        offset_dir = None
        if is_persp:
            if camera_loc:
                view_dir_unnormalized = camera_loc - v_vec
                if view_dir_unnormalized.length_squared > 1e-9:
                     offset_dir = view_dir_unnormalized.normalized()
        elif view_direction_ortho:
            offset_dir = view_direction_ortho

        if offset_dir and offset_dir.length_squared > 1e-9:
            offset_v = v_vec + offset_dir * offset_factor
            offset_verts.append(offset_v)
        else:
            offset_verts.append(v_vec)

    return offset_verts

def tag_redraw_all_view3d():
    """Forces redraw of all 3D views. Safe against context issues."""
    context = bpy.context
    if not context or not context.window_manager: return
    try:
        for window in context.window_manager.windows:
            screen = getattr(window, 'screen', None)
            if not screen: continue
            for area in getattr(screen, 'areas', []):
                if area.type == 'VIEW_3D':
                    tag_func = getattr(area, 'tag_redraw', None)
                    if tag_func:
                        try: tag_func()
                        except Exception: pass
    except Exception: pass

def clear_draw_data():
    """Clears the internal write buffer and the shared WM property."""
    global _draw_line_data_write
    _draw_line_data_write.clear()
    # Also clear the shared property on unregister/cleanup
    wm = getattr(bpy.context, 'window_manager', None)
    if wm and "fieldforge_draw_data" in wm:
        try:
            del wm["fieldforge_draw_data"]
            # print("DEBUG: Cleared fieldforge_draw_data from WM.") # DEBUG
        except (KeyError, TypeError, AttributeError):
            pass # Ignore if already deleted or WM invalid


# --- Main Draw Callback ---

def ff_draw_callback():
    """Draw callback function - Iterates through scene objects using bpy.context"""
    global _draw_line_data_write # Access write buffer
    context = bpy.context
    wm = getattr(context, 'window_manager', None) # Get WM early
    scene = getattr(context, 'scene', None)

    space_data = None; area = context.area
    if area and area.type == 'VIEW_3D': space_data = area.spaces.active
    else:
        screen = getattr(context, 'screen', None) or getattr(context.window, 'screen', None)
        if screen:
            for area_iter in getattr(screen, 'areas', []):
                if area_iter.type == 'VIEW_3D':
                    spaces = getattr(area_iter, 'spaces', None)
                    if spaces:
                         space_data = getattr(spaces, 'active', None)
                         if space_data: area = area_iter; break
    if not space_data: return
    region_3d = getattr(space_data, 'region_3d', None)
    if not scene or not region_3d: return

    try:
        view_matrix_inv = region_3d.view_matrix.inverted(); camera_location = view_matrix_inv.translation
    except Exception: camera_location = None

    # --- Get Theme Colors ---
    select_color_rgba = (1.0, 0.65, 0.0, 0.9); active_color_rgba = (1.0, 1.0, 1.0, 0.95)
    try:
        theme = context.preferences.themes[0]; select_rgb = theme.view_3d.object_selected; active_rgb = theme.view_3d.object_active
        if select_rgb and len(select_rgb) >= 3: select_color_rgba = (*select_rgb[:3], 0.9)
        if active_rgb and len(active_rgb) >= 3: active_color_rgba = (*active_rgb[:3], 0.95)
    except Exception: pass

    # --- Setup Shader & GPU State ---
    try: shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except Exception: print("FF Draw ERROR: Shader not found."); return
    old_blend=gpu.state.blend_get(); old_line_width=gpu.state.line_width_get(); old_depth_test=gpu.state.depth_test_get()
    gpu.state.blend_set('ALPHA'); gpu.state.depth_test_set('LESS_EQUAL')
    try:
        active_region = next((reg for reg in getattr(area, 'regions', []) if reg.type == 'WINDOW'), None)
        win_size = (active_region.width, active_region.height) if active_region and active_region.width > 0 and active_region.height > 0 else (max(1, context.window.width), max(1, context.window.height))
        shader.bind(); shader.uniform_float("viewportSize", win_size); shader.uniform_float("lineWidth", 2.0)
    except Exception as e:
        print(f"FF Draw ERROR: Shader setup failed: {e}")
        if old_line_width is not None: gpu.state.line_width_set(old_line_width);
        if old_blend is not None: gpu.state.blend_set(old_blend);
        if old_depth_test is not None: gpu.state.depth_test_set(old_depth_test); return

    DEPTH_OFFSET_FACTOR = 0.01

    # --- Prepare Data Storage ---
    _draw_line_data_write.clear() # Clear the WRITE buffer
    draw_list_non_selected = []; draw_list_selected_inactive = []; draw_list_active = []
    active_object = getattr(context.view_layer.objects, 'active', None)

    # --- Gather data loop ---
    for obj in scene.objects:
        try:
            current_view_layer = getattr(context, 'view_layer', None)
            if not obj or not current_view_layer or not obj.visible_get(view_layer=current_view_layer): continue
            if not utils.is_sdf_source(obj): continue
            parent_bounds = utils.find_parent_bounds(obj)
            if not parent_bounds or not utils.get_bounds_setting(parent_bounds, "sdf_show_source_empties"): continue
            sdf_type_prop = obj.get("sdf_type", "NONE");
            if sdf_type_prop == "NONE": continue

            obj_name=obj.name; is_selected=obj.select_get(); is_active=(active_object==obj)
            use_clearance=obj.get("sdf_use_clearance",False); use_morph=obj.get("sdf_use_morph",False)
            is_negative=obj.get("sdf_is_negative",False) and not use_clearance and not use_morph
            use_loft=obj.get("sdf_use_loft",False); base_color=(0.9,0.9,0.9,0.8)
            if is_negative or use_clearance: base_color=(1.0,0.2,0.2,0.8)
            elif use_loft or use_morph: base_color=(0.7,0.7,0.9,0.8)

            batches_to_draw=[]; line_segments_for_picking=[]
            mat=obj.matrix_world; obj_location=mat.translation
            avg_scale=max(1e-5,(abs(mat.col[0].length)+abs(mat.col[1].length)+abs(mat.col[2].length))/3.0)
            primitive_type='LINES'

            # --- Generate Vertices/Picking/Batches (ALL SHAPES) ---
            # Cube
            if sdf_type_prop == "cube":
                indices=constants.unit_cube_indices; local_verts=[Vector(v) for v in constants.unit_cube_verts]
                world_verts=[(mat @ v.to_4d()).xyz.copy() for v in local_verts]
                if world_verts:
                    for i,j in indices:
                        if i<len(world_verts) and j<len(world_verts): line_segments_for_picking.append((world_verts[i].copy(), world_verts[j].copy()))
                    # Indent try/except block
                    try:
                        offset_v = offset_vertices(world_verts, region_3d, camera_location, DEPTH_OFFSET_FACTOR)
                        batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_v}, indices=indices))
                    except Exception as e: print(f"FF Draw Batch Error (Cube): {obj_name} - {e}")
            # Sphere
            elif sdf_type_prop == "sphere":
                seg=24; r=0.5; lx=Vector((1,0,0)); ly=Vector((0,1,0)); lz=Vector((0,0,1))
                loops=[[(lx*math.cos(a)+ly*math.sin(a))*r for a in [(i/seg)*2*math.pi for i in range(seg)]],
                       [(ly*math.cos(a)+lz*math.sin(a))*r for a in [(i/seg)*2*math.pi for i in range(seg)]],
                       [(lx*math.cos(a)+lz*math.sin(a))*r for a in [(i/seg)*2*math.pi for i in range(seg)]]]
                for local_v in loops:
                    if not local_v: continue
                    world_l=[(mat @ Vector(v).to_4d()).xyz.copy() for v in local_v] # Ensure Vector() if needed
                    if world_l:
                        draw_p=[];
                        for i in range(len(world_l)): v1=world_l[i]; v2=world_l[(i+1)%len(world_l)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                        if draw_p:
                            # Indent try/except block
                            try:
                                batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                            except Exception as e: print(f"FF Draw Batch Error (Sphere): {obj_name} - {e}")
            # Cylinder
            elif sdf_type_prop == "cylinder":
                seg=16; l_top, l_bot = utils.create_unit_cylinder_cap_vertices(seg)
                w_top=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_top]; w_bot=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_bot]
                if w_top:
                    draw_p=[];
                    for i in range(len(w_top)): v1=w_top[i]; v2=w_top[(i+1)%len(w_top)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                    if draw_p:
                        # Indent try/except block
                        try:
                             batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                        except Exception as e: print(f"FF Draw Batch Error (Cyl Top): {obj_name} - {e}")
                if w_bot:
                    draw_p=[];
                    for i in range(len(w_bot)): v1=w_bot[i]; v2=w_bot[(i+1)%len(w_bot)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                    if draw_p:
                        # Indent try/except block
                        try:
                            batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                        except Exception as e: print(f"FF Draw Batch Error (Cyl Bot): {obj_name} - {e}")
                if w_top and w_bot: # Simplified Side Lines
                    try:
                        # --- Start Calculation ---
                        z_ax=mat.col[2].xyz.normalized()
                        # Ensure camera_location is valid
                        view_origin = camera_location if camera_location else Vector((0,0,10)) # Fallback view origin
                        view_d=(obj_location - view_origin)
                        view_d.z=0; # Project view direction approx
                        # Normalize safely
                        view_dir_normalized = view_d.normalized() if view_d.length > 1e-5 else Vector((1,0,0))

                        r_dir=z_ax.cross(view_dir_normalized).normalized() # Calculate right direction

                        t_max=-float('inf'); b_max=-float('inf')
                        t_min=float('inf'); b_min=float('inf')
                        # Initialize with first points for safety
                        t_pr=w_top[0] if w_top else Vector(); t_pl=w_top[0] if w_top else Vector()
                        b_pr=w_bot[0] if w_bot else Vector(); b_pl=w_bot[0] if w_bot else Vector()

                        # --- Corrected Loop with Proper Indentation ---
                        for i in range(len(w_top)): # Assuming w_top and w_bot have same length
                            dt=w_top[i].dot(r_dir)
                            db=w_bot[i].dot(r_dir) # Check corresponding bottom vertex
                            if dt > t_max:
                                t_max = dt
                                t_pr = w_top[i]
                            if dt < t_min: # Use separate if
                                t_min = dt
                                t_pl = w_top[i]
                            if db > b_max:
                                b_max = db
                                b_pr = w_bot[i]
                            if db < b_min: # Use separate if
                                b_min = db
                                b_pl = w_bot[i]
                        # --- End Corrected Loop ---

                        side1_top=t_pr; side1_bot=b_pr; side2_top=t_pl; side2_bot=b_pl # Use found vertices
                        sides_d=[side1_top, side1_bot, side2_top, side2_bot]
                        line_segments_for_picking.extend([(side1_top.copy(), side1_bot.copy()),(side2_top.copy(), side2_bot.copy())])

                        if sides_d:
                            # Indent try/except block
                            try:
                                batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(sides_d, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                            except Exception as e: print(f"FF Draw Batch Error (Cyl Side): {obj_name} - {e}")
                    except Exception as e_calc: print(f"FF Draw Calc Error (Cyl Sides): {obj_name} - {e_calc}")
            # Cone
            elif sdf_type_prop == "cone":
                seg=16; r=0.5; h=1.0; az=h; bz=0.0; l_bot_raw=utils.create_unit_circle_vertices_xy(seg)
                w_bot=[(mat @ Vector((v[0],v[1],bz)).to_4d()).xyz.copy() for v in l_bot_raw]
                if w_bot:
                    draw_p=[];
                    for i in range(len(w_bot)): v1=w_bot[i]; v2=w_bot[(i+1)%len(w_bot)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                    if draw_p:
                        # Indent try/except block
                        try:
                            batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                        except Exception as e: print(f"FF Draw Batch Error (Cone Base): {obj_name} - {e}")
                w_apex = (mat @ Vector((0,0,az)).to_4d()).xyz.copy()
                if w_bot: # Simplified Side Lines
                    try:
                        # --- Start Calculation ---
                        center_base=(mat @ Vector((0,0,bz)).to_4d()).xyz
                        # Ensure camera_location is valid before using
                        view_origin = camera_location if camera_location else Vector((0,0,10)) # Fallback view origin
                        view_dir=(center_base - view_origin)
                        view_dir.z=0; # Project view direction approx
                        # Normalize safely
                        view_dir_normalized = view_dir.normalized() if view_dir.length > 1e-5 else Vector((1,0,0))

                        z_axis=mat.col[2].xyz.normalized()
                        right_dir=z_axis.cross(view_dir_normalized).normalized() # Calculate right direction

                        b_max=-float('inf'); b_min=float('inf')
                        # Initialize with first point for safety
                        b_pr=w_bot[0] if w_bot else Vector();
                        b_pl=w_bot[0] if w_bot else Vector();

                        # --- Corrected Loop with Proper Indentation ---
                        for v in w_bot:
                            db = v.dot(right_dir)
                            if db > b_max:
                                b_max = db
                                b_pr = v # Store the vertex itself
                            # Use elif for exclusive check or separate if if conditions could overlap
                            if db < b_min: # Separate if, as min and max can be the same point initially
                                b_min = db
                                b_pl = v # Store the vertex itself
                        # --- End Corrected Loop ---

                        side1_base = b_pr; side2_base = b_pl # Use the found vertices
                        sides_d=[w_apex, side1_base, w_apex, side2_base]
                        line_segments_for_picking.extend([(w_apex.copy(), side1_base.copy()), (w_apex.copy(), side2_base.copy())])

                        if sides_d:
                            # Indent try/except block
                            try:
                                batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(sides_d, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                            except Exception as e: print(f"FF Draw Batch Error (Cone Side): {obj_name} - {e}")
                    except Exception as e_calc: print(f"FF Draw Calc Error (Cone Sides): {obj_name} - {e_calc}")
            elif sdf_type_prop == "rounded_box":
                cs = 4
                roundness_prop = obj.get("sdf_round_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_round_radius"])
                effective_prop_for_draw = min(max(roundness_prop, 0.0), 0.5)
                internal_draw_radius = effective_prop_for_draw * (0.25 / 0.5)

                lx = Vector((1, 0, 0)); ly = Vector((0, 1, 0)); lz = Vector((0, 0, 1))
                loops = [
                    utils.create_unit_rounded_rectangle_plane(lx, ly, internal_draw_radius, cs), # XY
                    utils.create_unit_rounded_rectangle_plane(lx, lz, internal_draw_radius, cs), # XZ
                    utils.create_unit_rounded_rectangle_plane(ly, lz, internal_draw_radius, cs), # YZ
                ]
                for local_v_loop in loops:
                    if not local_v_loop: continue
                    world_loop = [(mat @ Vector(v).to_4d()).xyz.copy() for v in local_v_loop]
                    if world_loop:
                        draw_p = []
                        for i in range(len(world_loop)):
                            v1 = world_loop[i]
                            v2 = world_loop[(i + 1) % len(world_loop)]
                            draw_p.extend([v1, v2])
                            line_segments_for_picking.append((v1.copy(), v2.copy()))
                        if draw_p:
                            try:
                                offset_loop = offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)
                                batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_loop}))
                            except Exception as e:
                                print(f"FF Draw Batch Error (RoundedBox Loop): {obj_name} - {e}")
            # Circle
            elif sdf_type_prop == "circle":
                seg=24; local_v=utils.create_unit_circle_vertices_xy(seg)
                world_o=[(mat @ Vector(v).to_4d()).xyz.copy() for v in local_v]
                if world_o:
                    draw_p=[];
                    for i in range(len(world_o)): v1=world_o[i]; v2=world_o[(i+1)%len(world_o)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                    if draw_p:
                        # Indent try/except block
                        try:
                            batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                        except Exception as e: print(f"FF Draw Batch Error (Circle): {obj_name} - {e}")
            # Ring
            elif sdf_type_prop == "ring":
                seg=24; r_o=0.5; r_i=obj.get("sdf_inner_radius",0.25); r_i=max(0.0, min(r_i, r_o-1e-5));
                l_outer=utils.create_unit_circle_vertices_xy(seg); l_inner=[(v[0]*r_i/r_o, v[1]*r_i/r_o, 0.0) for v in l_outer] if r_i>1e-6 else []
                w_outer=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_outer]; w_inner=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_inner]
                if w_outer:
                    draw_p=[];
                    for i in range(len(w_outer)): v1=w_outer[i]; v2=w_outer[(i+1)%len(w_outer)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                    if draw_p:
                        # Indent try/except block
                        try:
                            batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                        except Exception as e: print(f"FF Draw Batch Error (Ring Outer): {obj_name} - {e}")
                if w_inner:
                    draw_p=[];
                    for i in range(len(w_inner)): v1=w_inner[i]; v2=w_inner[(i+1)%len(w_inner)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                    if draw_p:
                        # Indent try/except block
                        try:
                            batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                        except Exception as e: print(f"FF Draw Batch Error (Ring Inner): {obj_name} - {e}")
            # Polygon
            elif sdf_type_prop == "polygon":
                sides = max(3, obj.get("sdf_sides", 6)); local_v=utils.create_unit_polygon_vertices_xy(sides)
                world_o=[(mat @ Vector(v).to_4d()).xyz.copy() for v in local_v]
                if world_o:
                    draw_p=[];
                    for i in range(len(world_o)): v1=world_o[i]; v2=world_o[(i+1)%len(world_o)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                    if draw_p:
                        # Indent try/except block
                        try:
                            batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                        except Exception as e: print(f"FF Draw Batch Error (Polygon): {obj_name} - {e}")
            # Half Space
            elif sdf_type_prop == "half_space":
                size=2.0; nl=0.5; wc=mat.translation; wx=mat.col[0].xyz; wy=mat.col[1].xyz; wz=mat.col[2].xyz
                plane_v=utils.create_rectangle_vertices(wc,wx.normalized(),wy.normalized(),size,size)
                if plane_v:
                    draw_p=[];
                    for i in range(len(plane_v)): v1=plane_v[i]; v2=plane_v[(i+1)%len(plane_v)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                    if draw_p:
                        # Indent try/except block
                        try:
                            batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                        except Exception as e: print(f"FF Draw Batch Error (HSpace Plane): {obj_name} - {e}")
                n_s=wc; n_e=wc+wz.normalized()*nl; arr=nl*0.2; n_b=n_e-wz.normalized()*arr;
                a1=n_b+wx.normalized()*arr*0.5; a2=n_b-wx.normalized()*arr*0.5; a3=n_b+wy.normalized()*arr*0.5; a4=n_b-wy.normalized()*arr*0.5
                norm_d=[n_s,n_e, n_e,a1, n_e,a2, n_e,a3, n_e,a4]; line_segments_for_picking.extend([(n_s.copy(),n_e.copy()),(n_e.copy(),a1.copy()),(n_e.copy(),a2.copy()),(n_e.copy(),a3.copy()),(n_e.copy(),a4.copy())])
                if norm_d:
                    # Indent try/except block
                    try:
                        batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(norm_d, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                    except Exception as e: print(f"FF Draw Batch Error (HSpace Norm): {obj_name} - {e}")
            # Torus
            elif sdf_type_prop == "torus":
                mseg=24; minor_seg=12; r_maj=max(0.01,obj.get("sdf_torus_major_radius",0.35)); r_min=max(0.005,obj.get("sdf_torus_minor_radius",0.15)); r_min=min(r_min, r_maj-1e-5)
                l_main=[(math.cos(a)*r_maj, math.sin(a)*r_maj, 0.0) for a in [(i/mseg)*2*math.pi for i in range(mseg)]]
                w_main=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_main]
                if w_main:
                    draw_p=[];
                    for i in range(len(w_main)): v1=w_main[i]; v2=w_main[(i+1)%len(w_main)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                    if draw_p:
                        # Indent try/except block
                        try:
                            batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                        except Exception as e: print(f"FF Draw Batch Error (Torus Main): {obj_name} - {e}")
                if r_min > 1e-5:
                    l_centers=[Vector((0,r_maj,0)),Vector((0,-r_maj,0)),Vector((r_maj,0,0)),Vector((-r_maj,0,0))]
                    l_tangents=[Vector((1,0,0)),Vector((-1,0,0)),Vector((0,1,0)),Vector((0,-1,0))]; l_axis1=Vector((0,0,1))
                    for i, l_center in enumerate(l_centers):
                        l_tangent=l_tangents[i]; l_axis2=l_tangent.cross(l_axis1).normalized()
                        l_minor=[(l_center + (l_axis1*math.cos(a) + l_axis2*math.sin(a))*r_min) for a in [(j/minor_seg)*2*math.pi for j in range(minor_seg)]]
                        w_minor=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_minor]
                        if w_minor:
                            draw_p=[];
                            for k in range(len(w_minor)): v1=w_minor[k]; v2=w_minor[(k+1)%len(w_minor)]; draw_p.extend([v1,v2]); line_segments_for_picking.append((v1.copy(),v2.copy()))
                            if draw_p:
                                # Indent try/except block
                                try:
                                    batches_to_draw.append(batch_for_shader(shader, primitive_type, {"pos": offset_vertices(draw_p, region_3d, camera_location, DEPTH_OFFSET_FACTOR)}))
                                except Exception as e: print(f"FF Draw Batch Error (Torus Minor): {obj_name} - {e}")

            # --- Store and Sort ---
            if line_segments_for_picking:
                _draw_line_data_write[obj.name] = line_segments_for_picking
            if batches_to_draw:
                # Check if this object is the active one *in Blender's context*
                is_blender_active = (active_object == obj)

                if is_blender_active and is_selected:
                    # Draw using ACTIVE color (white) only if it's Blender's active object AND selected.
                    draw_list_active.append((batches_to_draw, obj_name))
                elif is_selected:
                    # Draw using SELECTED color (orange) if it's selected but not active.
                    draw_list_selected_inactive.append((batches_to_draw, obj_name))
                else:
                    # Draw using NON-SELECTED color (grey/red/blue based on mode) if it's not selected at all.
                    draw_list_non_selected.append((batches_to_draw, base_color, obj_name))

        # --- Error Handling for Outer Loop ---
        except ReferenceError: continue
        except Exception as e_outer: print(f"FF Draw Error (Outer Loop): {obj.name if obj else '?'} - {type(e_outer).__name__}: {e_outer}")

    if wm: # Check if window manager is valid
        try:
            # Store a shallow copy for the operator to read
            wm["fieldforge_draw_data"] = _draw_line_data_write.copy()
            # print(f"--- ff_draw_callback Updated WM Property --- Keys: {list(wm['fieldforge_draw_data'].keys())}") # DEBUG
        except Exception as e_prop:
             print(f"FF Draw ERROR: Failed to set WM property: {e_prop}")
    # else: print("FF Draw WARN: No Window Manager found, cannot store draw data.") # DEBUG
    # --- Drawing Passes ---
    try:
        shader.bind()
        for batches, color, obj_name in draw_list_non_selected:
             shader.uniform_float("color", color)
             for batch in batches: batch.draw(shader)
        shader.uniform_float("color", select_color_rgba)
        for batches, obj_name in draw_list_selected_inactive:
             for batch in batches: batch.draw(shader)
        shader.uniform_float("color", active_color_rgba)
        for batches, obj_name in draw_list_active:
             for batch in batches: batch.draw(shader)
    except Exception as e_draw: print(f"FF Draw Error (Drawing Pass): {e_draw}")
    finally: # Restore state
        if old_line_width is not None: gpu.state.line_width_set(old_line_width)
        if old_blend is not None: gpu.state.blend_set(old_blend)
        if old_depth_test is not None: gpu.state.depth_test_set(old_depth_test)