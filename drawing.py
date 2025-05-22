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
        shader.bind(); shader.uniform_float("viewportSize", win_size); shader.uniform_float("lineWidth", 1.0)
    except Exception as e:
        print(f"FF Draw ERROR: Shader setup failed: {e}")
        if old_line_width is not None: gpu.state.line_width_set(old_line_width);
        if old_blend is not None: gpu.state.blend_set(old_blend);
        if old_depth_test is not None: gpu.state.depth_test_set(old_depth_test); return

    DEPTH_OFFSET_FACTOR = 0.01

    # --- Prepare Data Storage ---
    _draw_line_data_write.clear() # Clear the WRITE buffer
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

            line_segments_for_picking_for_this_obj = []
            # This list will store all vertices for drawing this object if it's selected/active
            # For indexed shapes like cube, this won't be used directly for batch creation.
            all_world_verts_for_batch_if_selected = [] 
            
            # Specific storage for indexed shapes (like cube)
            indexed_world_verts = None # e.g., list of Vector for cube vertices
            indices_for_batch = None   # e.g., constants.unit_cube_indices

            mat=obj.matrix_world
            primitive_type='LINES'

            # --- Generate Geometry Data ---
            # Cube
            if sdf_type_prop == "cube":
                indices_for_batch = constants.unit_cube_indices # Store for later
                local_verts=[Vector(v) for v in constants.unit_cube_verts]
                indexed_world_verts =[(mat @ v.to_4d()).xyz.copy() for v in local_verts] # Store for later
                if indexed_world_verts:
                    for i,j in indices_for_batch:
                        if i<len(indexed_world_verts) and j<len(indexed_world_verts): 
                            line_segments_for_picking_for_this_obj.append((indexed_world_verts[i].copy(), indexed_world_verts[j].copy()))
            # Sphere
            elif sdf_type_prop == "sphere":
                seg=24; r=0.5; lx=Vector((1,0,0)); ly=Vector((0,1,0)); lz=Vector((0,0,1))
                loops=[[(lx*math.cos(a)+ly*math.sin(a))*r for a in [(i/seg)*2*math.pi for i in range(seg)]],
                       [(ly*math.cos(a)+lz*math.sin(a))*r for a in [(i/seg)*2*math.pi for i in range(seg)]],
                       [(lx*math.cos(a)+lz*math.sin(a))*r for a in [(i/seg)*2*math.pi for i in range(seg)]]]
                for local_v_loop in loops:
                    if not local_v_loop: continue
                    world_l=[(mat @ Vector(v).to_4d()).xyz.copy() for v in local_v_loop] 
                    if world_l:
                        for i in range(len(world_l)): 
                            v1=world_l[i]; v2=world_l[(i+1)%len(world_l)]
                            line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                            all_world_verts_for_batch_if_selected.extend([v1,v2])
            # Cylinder
            elif sdf_type_prop == "cylinder":
                seg=16; l_top, l_bot = utils.create_unit_cylinder_cap_vertices(seg)
                w_top_cyl=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_top]; 
                w_bot_cyl=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_bot]
                if w_top_cyl:
                    for i in range(len(w_top_cyl)): 
                        v1=w_top_cyl[i]; v2=w_top_cyl[(i+1)%len(w_top_cyl)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1,v2])
                if w_bot_cyl:
                    for i in range(len(w_bot_cyl)): 
                        v1=w_bot_cyl[i]; v2=w_bot_cyl[(i+1)%len(w_bot_cyl)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1,v2])
                if w_top_cyl and w_bot_cyl: 
                    try:
                        z_ax=mat.col[2].xyz.normalized()
                        obj_loc_cyl = mat.translation
                        view_origin = camera_location if camera_location else Vector((0,0,10)) 
                        view_d=(obj_loc_cyl - view_origin)
                        view_d.z=0; 
                        view_dir_normalized = view_d.normalized() if view_d.length > 1e-5 else Vector((1,0,0))
                        r_dir=z_ax.cross(view_dir_normalized).normalized()
                        t_max=-float('inf'); b_max=-float('inf'); t_min=float('inf'); b_min=float('inf')
                        t_pr=w_top_cyl[0]; t_pl=w_top_cyl[0]; b_pr=w_bot_cyl[0]; b_pl=w_bot_cyl[0]
                        for i in range(len(w_top_cyl)): 
                            dt=w_top_cyl[i].dot(r_dir); db=w_bot_cyl[i].dot(r_dir) 
                            if dt > t_max: t_max = dt; t_pr = w_top_cyl[i]
                            if dt < t_min: t_min = dt; t_pl = w_top_cyl[i]
                            if db > b_max: b_max = db; b_pr = w_bot_cyl[i]
                            if db < b_min: b_min = db; b_pl = w_bot_cyl[i]
                        sides_d_cyl=[t_pr, b_pr, t_pl, b_pl] # Corrected order for two lines
                        line_segments_for_picking_for_this_obj.append((t_pr.copy(), b_pr.copy()))
                        line_segments_for_picking_for_this_obj.append((t_pl.copy(), b_pl.copy()))
                        all_world_verts_for_batch_if_selected.extend(sides_d_cyl)
                    except Exception as e_calc: print(f"FF Draw Calc Error (Cyl Sides): {obj_name} - {e_calc}")
            # Cone
            elif sdf_type_prop == "cone":
                seg=16; h_draw_cone=1.0; apex_z_local_cone=h_draw_cone; base_z_local_cone=0.0; 
                l_bot_raw_cone=utils.create_unit_circle_vertices_xy(seg)
                l_bot_transformed_z_cone = [(v[0], v[1], base_z_local_cone) for v in l_bot_raw_cone]
                w_bot_cone=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_bot_transformed_z_cone]
                if w_bot_cone:
                    for i in range(len(w_bot_cone)): 
                        v1=w_bot_cone[i]; v2=w_bot_cone[(i+1)%len(w_bot_cone)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1,v2])
                w_apex_cone = (mat @ Vector((0,0,apex_z_local_cone)).to_4d()).xyz.copy()
                if w_bot_cone: 
                    try:
                        obj_loc_cone = mat.translation
                        center_base_cone=(mat @ Vector((0,0,base_z_local_cone)).to_4d()).xyz
                        view_origin_cone = camera_location if camera_location else Vector((0,0,10))
                        view_dir_cone=(obj_loc_cone - view_origin_cone)
                        view_dir_cone.z=0; 
                        view_dir_normalized_cone = view_dir_cone.normalized() if view_dir_cone.length > 1e-5 else Vector((1,0,0))
                        z_axis_cone=mat.col[2].xyz.normalized()
                        right_dir_cone=z_axis_cone.cross(view_dir_normalized_cone).normalized()
                        b_max_cone=-float('inf'); b_min_cone=float('inf')
                        b_pr_cone=w_bot_cone[0]; b_pl_cone=w_bot_cone[0];
                        for v_base_world_cone in w_bot_cone:
                            db_cone = v_base_world_cone.dot(right_dir_cone)
                            if db_cone > b_max_cone: b_max_cone = db_cone; b_pr_cone = v_base_world_cone
                            if db_cone < b_min_cone: b_min_cone = db_cone; b_pl_cone = v_base_world_cone
                        sides_d_cone=[w_apex_cone, b_pr_cone, w_apex_cone, b_pl_cone]
                        line_segments_for_picking_for_this_obj.append((w_apex_cone.copy(), b_pr_cone.copy()))
                        line_segments_for_picking_for_this_obj.append((w_apex_cone.copy(), b_pl_cone.copy()))
                        all_world_verts_for_batch_if_selected.extend(sides_d_cone)
                    except Exception as e_calc: print(f"FF Draw Calc Error (Cone Sides): {obj_name} - {e_calc}")
            elif sdf_type_prop == "pyramid":
                local_base_verts_pyramid = [
                    Vector((-0.5, -0.5, 0.0)), Vector(( 0.5, -0.5, 0.0)),
                    Vector(( 0.5,  0.5, 0.0)), Vector((-0.5,  0.5, 0.0))
                ]
                local_apex_pyramid = Vector((0.0, 0.0, 1.0))

                world_base_verts_pyramid = [(mat @ v.to_4d()).xyz.copy() for v in local_base_verts_pyramid]
                world_apex_pyramid = (mat @ local_apex_pyramid.to_4d()).xyz.copy()

                for i in range(len(world_base_verts_pyramid)):
                    v1 = world_base_verts_pyramid[i]
                    v2 = world_base_verts_pyramid[(i + 1) % len(world_base_verts_pyramid)]
                    line_segments_for_picking_for_this_obj.append((v1.copy(), v2.copy()))
                    all_world_verts_for_batch_if_selected.extend([v1, v2])
                
                for v_base in world_base_verts_pyramid:
                    line_segments_for_picking_for_this_obj.append((v_base.copy(), world_apex_pyramid.copy()))
                    all_world_verts_for_batch_if_selected.extend([v_base, world_apex_pyramid])
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
                        for i in range(len(world_loop)):
                            v1 = world_loop[i]
                            v2 = world_loop[(i + 1) % len(world_loop)]
                            line_segments_for_picking_for_this_obj.append((v1.copy(), v2.copy()))
                            all_world_verts_for_batch_if_selected.extend([v1, v2])
            
            elif sdf_type_prop == "circle":
                seg_circle=24; local_v_circle=utils.create_unit_circle_vertices_xy(seg_circle)
                world_o_circle=[(mat @ Vector(v).to_4d()).xyz.copy() for v in local_v_circle]
                if world_o_circle:
                    for i in range(len(world_o_circle)): 
                        v1=world_o_circle[i]; v2=world_o_circle[(i+1)%len(world_o_circle)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1,v2])

            elif sdf_type_prop == "ring":
                seg_ring=24; r_o_ring=0.5; 
                r_i_prop_ring = obj.get("sdf_inner_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_inner_radius"])
                r_i_ring = max(0.0, min(float(r_i_prop_ring), r_o_ring - 1e-5))
                l_outer_local_xy_ring=utils.create_unit_circle_vertices_xy(seg_ring)
                l_inner_local_xy_ring=[(v[0]*r_i_ring/r_o_ring, v[1]*r_i_ring/r_o_ring, 0.0) for v in l_outer_local_xy_ring] if r_i_ring > 1e-6 else []
                w_outer_ring=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_outer_local_xy_ring]; 
                w_inner_ring=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_inner_local_xy_ring]
                if w_outer_ring:
                    for i in range(len(w_outer_ring)): 
                        v1=w_outer_ring[i]; v2=w_outer_ring[(i+1)%len(w_outer_ring)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1,v2])
                if w_inner_ring:
                    for i in range(len(w_inner_ring)): 
                        v1=w_inner_ring[i]; v2=w_inner_ring[(i+1)%len(w_inner_ring)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1,v2])
            
            elif sdf_type_prop == "polygon":
                sides_poly = max(3, obj.get("sdf_sides", constants.DEFAULT_SOURCE_SETTINGS["sdf_sides"]))
                local_v_poly=utils.create_unit_polygon_vertices_xy(sides_poly)
                world_o_poly=[(mat @ Vector(v).to_4d()).xyz.copy() for v in local_v_poly]
                if world_o_poly:
                    for i in range(len(world_o_poly)): 
                        v1=world_o_poly[i]; v2=world_o_poly[(i+1)%len(world_o_poly)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1,v2])

            elif sdf_type_prop == "text":
                text_string = obj.get("sdf_text_string", constants.DEFAULT_SOURCE_SETTINGS["sdf_text_string"])
                if not text_string.strip(): continue # Don't draw if empty

                # Approximate unit bounds for text (height ~1, width estimated)
                # This matches the rough centering in reconstruct_shape
                num_chars = len(text_string)
                est_unit_width = num_chars * 0.7  # Very rough estimate
                est_unit_height = 1.0 # Based on libfive's font definition

                half_w = est_unit_width / 2.0
                half_h = est_unit_height / 2.0

                # Local corners of the bounding box (2D in XY plane, centered around where text starts)
                # The text in reconstruct_shape starts at (-est_width/2, -0.5)
                # So the box should be relative to that.
                # If text starts at (sx, sy) and has width w, height h, then box is
                # (sx, sy), (sx+w, sy), (sx+w, sy+h), (sx, sy+h)
                # Our start_pos_x = -half_w, start_pos_y = -0.5 (baseline)
                # So, local corners are approximately:
                # (-half_w, -0.5) , (half_w, -0.5), (half_w, 0.5), (-half_w, 0.5)
                # This centers the box horizontally and makes its vertical center at y=0
                local_rect_verts = [
                    Vector((-half_w, -half_h, 0.0)), Vector(( half_w, -half_h, 0.0)),
                    Vector(( half_w,  half_h, 0.0)), Vector((-half_w,  half_h, 0.0))
                ]
                
                world_rect_verts = [(mat @ v.to_4d()).xyz.copy() for v in local_rect_verts]

                if world_rect_verts:
                    for i in range(len(world_rect_verts)):
                        v1 = world_rect_verts[i]
                        v2 = world_rect_verts[(i + 1) % len(world_rect_verts)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(), v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1, v2])

            elif sdf_type_prop == "half_space":
                draw_plane_size_hs=2.0; arrow_len_factor_hs=0.5 
                plane_verts_local_hs = [
                    Vector(( draw_plane_size_hs/2,  draw_plane_size_hs/2, 0)), Vector((-draw_plane_size_hs/2,  draw_plane_size_hs/2, 0)),
                    Vector((-draw_plane_size_hs/2, -draw_plane_size_hs/2, 0)), Vector(( draw_plane_size_hs/2, -draw_plane_size_hs/2, 0)) ]
                plane_verts_world_hs = [(mat @ v.to_4d()).xyz for v in plane_verts_local_hs]
                if plane_verts_world_hs:
                    for i in range(len(plane_verts_world_hs)): 
                        v1=plane_verts_world_hs[i]; v2=plane_verts_world_hs[(i+1)%len(plane_verts_world_hs)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1,v2])
                arrow_start_local_hs = Vector((0,0,0)); arrow_end_local_hs = Vector((0,0, arrow_len_factor_hs))
                arrow_start_world_hs = (mat @ arrow_start_local_hs.to_4d()).xyz; arrow_end_world_hs = (mat @ arrow_end_local_hs.to_4d()).xyz
                arrow_head_size_hs = arrow_len_factor_hs * 0.2
                ah1_local_hs = Vector(( arrow_head_size_hs,0,arrow_len_factor_hs-arrow_head_size_hs*1.5)); ah2_local_hs = Vector((-arrow_head_size_hs,0,arrow_len_factor_hs-arrow_head_size_hs*1.5))
                ah3_local_hs = Vector((0,arrow_head_size_hs,arrow_len_factor_hs-arrow_head_size_hs*1.5)); ah4_local_hs = Vector((0,-arrow_head_size_hs,arrow_len_factor_hs-arrow_head_size_hs*1.5))
                ah1w = (mat @ ah1_local_hs.to_4d()).xyz; ah2w = (mat @ ah2_local_hs.to_4d()).xyz; ah3w = (mat @ ah3_local_hs.to_4d()).xyz; ah4w = (mat @ ah4_local_hs.to_4d()).xyz
                hs_arrow_lines = [
                    (arrow_start_world_hs, arrow_end_world_hs), (arrow_end_world_hs, ah1w),
                    (arrow_end_world_hs, ah2w), (arrow_end_world_hs, ah3w), (arrow_end_world_hs, ah4w) ]
                for v_start, v_end in hs_arrow_lines:
                    line_segments_for_picking_for_this_obj.append((v_start.copy(), v_end.copy()))
                    all_world_verts_for_batch_if_selected.extend([v_start, v_end])

            elif sdf_type_prop == "torus":
                mseg_torus=24; minor_seg_torus=12
                r_maj_torus=max(0.01,float(obj.get("sdf_torus_major_radius",0.35))) # Ensure float conversion
                r_min_torus=max(0.005,float(obj.get("sdf_torus_minor_radius",0.15))) # Ensure float conversion
                r_min_torus=min(r_min_torus, r_maj_torus-1e-5)
                
                # Main ring
                l_main_local_torus=[(math.cos(a)*r_maj_torus, math.sin(a)*r_maj_torus, 0.0) for a in [(i/mseg_torus)*2*math.pi for i in range(mseg_torus)]]
                w_main_torus=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_main_local_torus]
                if w_main_torus:
                    for i in range(len(w_main_torus)): 
                        v1=w_main_torus[i]; v2=w_main_torus[(i+1)%len(w_main_torus)]
                        line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                        all_world_verts_for_batch_if_selected.extend([v1,v2])
                
                # Minor rings
                if r_min_torus > 1e-5:
                    # Original logic for l_centers, l_tangents, l_axis1
                    l_centers_local_torus=[Vector((0,r_maj_torus,0)),Vector((0,-r_maj_torus,0)),Vector((r_maj_torus,0,0)),Vector((-r_maj_torus,0,0))]
                    l_tangents_local_torus=[Vector((1,0,0)),Vector((-1,0,0)),Vector((0,1,0)),Vector((0,-1,0))]
                    l_axis1_local_torus=Vector((0,0,1)) # Z-axis for XY plane circles

                    for i, l_center_local in enumerate(l_centers_local_torus):
                        l_tangent_local=l_tangents_local_torus[i]
                        l_axis2_local=l_tangent_local.cross(l_axis1_local_torus).normalized() # Plane for minor circle

                        # Generate points for this minor circle
                        l_minor_local_points = []
                        for j in range(minor_seg_torus):
                            angle_minor = (j/minor_seg_torus)*2*math.pi
                            # Point on the minor circle in its local plane, then add center
                            point = l_center_local + (l_axis1_local_torus * math.cos(angle_minor) + l_axis2_local * math.sin(angle_minor)) * r_min_torus
                            l_minor_local_points.append(point)
                        
                        w_minor_torus_points=[(mat @ Vector(v).to_4d()).xyz.copy() for v in l_minor_local_points]
                        if w_minor_torus_points:
                            for k in range(len(w_minor_torus_points)): 
                                v1=w_minor_torus_points[k]; v2=w_minor_torus_points[(k+1)%len(w_minor_torus_points)]
                                line_segments_for_picking_for_this_obj.append((v1.copy(),v2.copy()))
                                all_world_verts_for_batch_if_selected.extend([v1,v2])
            if line_segments_for_picking_for_this_obj:
                _draw_line_data_write[obj.name] = line_segments_for_picking_for_this_obj
            
            if is_selected or is_active:
                current_draw_color = active_color_rgba if is_active else select_color_rgba
                batches_to_draw_for_this_obj = [] # Re-init for this object's draw pass

                try:
                    if sdf_type_prop == "cube" and indexed_world_verts and indices_for_batch:
                        offset_v = offset_vertices(indexed_world_verts, region_3d, camera_location, DEPTH_OFFSET_FACTOR)
                        batches_to_draw_for_this_obj.append(batch_for_shader(shader, primitive_type, {"pos": offset_v}, indices=indices_for_batch))
                    elif all_world_verts_for_batch_if_selected: # For all other non-indexed shapes
                        offset_v = offset_vertices(all_world_verts_for_batch_if_selected, region_3d, camera_location, DEPTH_OFFSET_FACTOR)
                        batches_to_draw_for_this_obj.append(batch_for_shader(shader, primitive_type, {"pos": offset_v}))
                except Exception as e: 
                    print(f"FF Draw Batch Create Error ({sdf_type_prop}): {obj_name} - {e}")

                if batches_to_draw_for_this_obj:
                    shader.uniform_float("color", current_draw_color)
                    for batch in batches_to_draw_for_this_obj:
                        batch.draw(shader)
        except ReferenceError: continue
        except Exception as e_outer: print(f"FF Draw Error (Outer Loop): {obj.name if obj else '?'} - {type(e_outer).__name__}: {e_outer}")

    if wm: # Check if window manager is valid
        try:
            # Store a shallow copy for the operator to read
            wm["fieldforge_draw_data"] = _draw_line_data_write.copy()
            # print(f"--- ff_draw_callback Updated WM Property --- Keys: {list(wm['fieldforge_draw_data'].keys())}") # DEBUG
        except Exception as e_prop:
             print(f"FF Draw ERROR: Failed to set WM property: {e_prop}")
        finally: # Restore state
            if old_line_width is not None: gpu.state.line_width_set(old_line_width)
            if old_blend is not None: gpu.state.blend_set(old_blend)
            if old_depth_test is not None: gpu.state.depth_test_set(old_depth_test)