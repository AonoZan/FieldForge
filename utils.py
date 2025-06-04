"""
General utility and helper functions for the FieldForge addon.
"""

import bpy
import math
from mathutils import Vector, Matrix

from . import constants

# --- Blender Object/Hierarchy Helpers ---

def get_all_bounds_objects(context: bpy.types.Context):
    """ Generator yielding all SDF Bounds objects in the current scene """
    for obj in context.scene.objects:
        if obj.get(constants.SDF_BOUNDS_MARKER, False):
            yield obj

def find_result_object(context: bpy.types.Context, result_name: str) -> bpy.types.Object | None:
    """ Finds a result object by its name in the current scene """
    if not result_name:
        return None
    return context.scene.objects.get(result_name)

def find_parent_bounds(start_obj: bpy.types.Object) -> bpy.types.Object | None:
    """
    Traverses up the hierarchy from start_obj to find the root SDF Bounds object.
    Returns the Bounds object or None if not part of an SDF hierarchy.
    """
    obj = start_obj
    # Limit search depth to prevent infinite loops in case of weird parenting cycles
    max_depth = 100
    count = 0
    while obj and count < max_depth:
        if obj.get(constants.SDF_BOUNDS_MARKER, False):
            return obj
        obj = obj.parent
        count += 1
    return None # Not part of a known bounds hierarchy


def is_sdf_bounds(obj: bpy.types.Object) -> bool:
    """ Checks if an object is configured as an SDF Bounds Empty """
    return obj and obj.type == 'EMPTY' and obj.get(constants.SDF_BOUNDS_MARKER, False)

def is_sdf_source(obj: bpy.types.Object) -> bool:
    """ Checks if an object is configured as an SDF source Empty """
    # Check object exists, is an EMPTY type, and has the marker property
    return obj and obj.type == 'EMPTY' and obj.get(constants.SDF_PROPERTY_MARKER, False)

def is_sdf_group(obj: bpy.types.Object) -> bool:
    """ Checks if an object is configured as an SDF Group Empty """
    return obj and obj.type == 'EMPTY' and obj.get(constants.SDF_GROUP_MARKER, False)

def is_sdf_canvas(obj: bpy.types.Object) -> bool:
    """ Checks if an object is configured as an SDF Canvas Empty """
    return obj and obj.type == 'EMPTY' and obj.get(constants.SDF_CANVAS_MARKER, False)

def is_valid_2d_loft_source(obj: bpy.types.Object) -> bool:
    """Checks if an object is an SDF source and is a 2D type eligible for lofting."""
    if not is_sdf_source(obj):
        return False
    sdf_type = obj.get("sdf_type", "")
    return sdf_type in constants._2D_SHAPE_TYPES

def get_effective_sdf_object(obj: bpy.types.Object) -> bpy.types.Object | None:
    """
    If obj is linked, returns its link target if compatible. Otherwise, returns obj itself.
    Returns None if obj is None or the link target is not found.
    """
    if not obj:
        return None
    
    link_target_name = obj.get(constants.SDF_LINK_TARGET_NAME_PROP, "")
    if not link_target_name:
        return obj 

    current_context = bpy.context
    if not hasattr(current_context, 'scene') or not current_context.scene :
        # Cannot resolve link without a scene context, return obj itself
        return obj

    target_obj = current_context.scene.objects.get(link_target_name)
    if not target_obj: 
        return obj 

    if target_obj == obj: 
        return obj 
    
    is_obj_source = is_sdf_source(obj)
    is_target_source = is_sdf_source(target_obj)
    is_obj_group = is_sdf_group(obj)
    is_target_group = is_sdf_group(target_obj)
    is_obj_canvas = is_sdf_canvas(obj)
    is_target_canvas = is_sdf_canvas(target_obj)
    is_obj_bounds = is_sdf_bounds(obj)
    is_target_bounds = is_sdf_bounds(target_obj)

    compatible_link = False
    if (is_obj_source and is_target_source) or \
       (is_obj_group and is_target_group) or \
       (is_obj_canvas and is_target_canvas) or \
       (is_obj_bounds and is_target_bounds):
        compatible_link = True
    
    return target_obj if compatible_link else obj

def get_sdf_param(obj: bpy.types.Object, param_key: str, default_value):
    """
    Gets an SDF parameter, respecting linking.
    """
    effective_obj = get_effective_sdf_object(obj)
    if not effective_obj: 
        return default_value
    return effective_obj.get(param_key, default_value)

def is_sdf_linked(obj: bpy.types.Object) -> bool:
    if not obj: return False
    link_target_name = obj.get(constants.SDF_LINK_TARGET_NAME_PROP, "")
    if link_target_name:
        current_context = bpy.context
        if not hasattr(current_context, 'scene') or not current_context.scene : return False
        target_obj = current_context.scene.objects.get(link_target_name)
        return target_obj is not None and get_effective_sdf_object(obj) == target_obj and target_obj != obj
    return False

def get_bounds_setting(bounds_obj: bpy.types.Object, setting_key: str):
    """
    Safely retrieves a setting from a bounds object's custom properties.
    Uses get_sdf_param to respect linking.
    """
    if not bounds_obj or not setting_key:
        return constants.DEFAULT_SETTINGS.get(setting_key)
    return get_sdf_param(bounds_obj, setting_key, constants.DEFAULT_SETTINGS.get(setting_key))

def initiate_settings(obj, defaults):
    for key, default_value in defaults.items():
        try:
            obj[key] = default_value
        except TypeError as e:
            print(f"FieldForge WARN: Could not set default property '{key}' on {obj.name}: {e}. Value: {default_value}")

def compare_matrices(mat1: Matrix | None, mat2: Matrix | None, tolerance=constants.CACHE_PRECISION) -> bool:
    """ Compare two 4x4 matrices element-wise with a tolerance for floats. """
    if mat1 is None or mat2 is None:
        return mat1 is mat2 # True only if both are None
    # Ensure they are Matrix objects (basic type check)
    if not isinstance(mat1, Matrix) or not isinstance(mat2, Matrix):
        return False
    # Compare elements
    for i in range(4):
        for j in range(4):
            if abs(mat1[i][j] - mat2[i][j]) > tolerance:
                return False
    return True

def compare_vectors(vec1: Vector | None, vec2: Vector | None, tolerance=constants.CACHE_PRECISION) -> bool:
    """ Compare two Vectors element-wise with a tolerance for floats. """
    if vec1 is None or vec2 is None:
        return vec1 is vec2
    if not isinstance(vec1, Vector) or not isinstance(vec2, Vector):
        return False
    if len(vec1) != len(vec2):
        return False
    for i in range(len(vec1)):
        if abs(vec1[i] - vec2[i]) > tolerance:
            return False
    return True

def compare_dicts(dict1: dict | None, dict2: dict | None, tolerance=constants.CACHE_PRECISION) -> bool:
    """
    Compare dictionaries (shallow), checking floats, vectors, matrices
    with tolerance. Handles basic types (int, str, bool) directly.
    """
    if dict1 is None or dict2 is None:
        return dict1 is dict2
    if not isinstance(dict1, dict) or not isinstance(dict2, dict):
        return False # Ensure both are dicts
    if set(dict1.keys()) != set(dict2.keys()):
        return False # Different keys

    for key, val1 in dict1.items():
        val2 = dict2.get(key)

        # Type-specific comparisons with tolerance
        if isinstance(val1, float):
            if not isinstance(val2, (float, int)) or abs(val1 - val2) > tolerance: return False
        elif isinstance(val1, Matrix):
            if not compare_matrices(val1, val2, tolerance): return False
        elif isinstance(val1, Vector):
             if not compare_vectors(val1, val2, tolerance): return False
        elif type(val1) != type(val2):
            return False
        elif val1 != val2:
            return False
    return True


# --- Drawing Helpers ---

def create_circle_vertices(center: Vector, right: Vector, up: Vector, radius: float, segments: int) -> list[Vector]:
    """ Generates world-space vertices for a circle defined by center, orthogonal axes, radius, and segments. """
    if segments < 3: return []
    vertices = []
    if radius <= 1e-6: return [center.copy() for _ in range(segments)] if segments > 0 else []
    try:
        for i in range(segments):
            angle = (i / segments) * 2 * math.pi
            offset = (right * math.cos(angle) + up * math.sin(angle)) * radius
            vertices.append(center + offset)
    except Exception: return []
    return vertices

def create_rectangle_vertices(center: Vector, right: Vector, up: Vector, width: float, height: float) -> list[Vector]:
    half_w = max(0.0, width / 2.0); half_h = max(0.0, height / 2.0)
    tr_offset = (right * half_w) + (up * half_h); tl_offset = (-right * half_w) + (up * half_h)
    return [center + tr_offset, center + tl_offset, center - tr_offset, center - tl_offset]

def create_unit_circle_vertices_xy(segments: int) -> list[tuple[float, float, float]]:
    if segments < 3: return []
    vertices = []; radius = 0.5
    for i in range(segments):
        angle = (i / segments) * 2 * math.pi
        vertices.append( (math.cos(angle) * radius, math.sin(angle) * radius, 0.0) )
    return vertices

def create_unit_polygon_vertices_xy(segments: int) -> list[tuple[float, float, float]]:
    if segments < 3: return []
    vertices = []; radius = 0.5; angle_offset = -math.pi / 2.0 
    if segments % 2 == 0: angle_offset += (math.pi / segments) 
    for i in range(segments):
        angle = angle_offset + (i / segments) * 2 * math.pi
        vertices.append( (math.cos(angle) * radius, math.sin(angle) * radius, 0.0) )
    return vertices

def create_unit_cylinder_cap_vertices(segments: int) -> tuple[list, list]:
    top_verts = []; bot_verts = []; radius = 0.5; half_height = 0.5
    if segments >= 3:
        for i in range(segments):
            angle = (i / segments) * 2 * math.pi
            x = math.cos(angle) * radius; y = math.sin(angle) * radius
            top_verts.append( (x, y, half_height) ); bot_verts.append( (x, y, -half_height) )
    return top_verts, bot_verts

def create_unit_rounded_rectangle_plane(local_right: Vector, local_up: Vector, draw_radius: float, segments_per_corner: int) -> list[Vector]:
    """
    Generates local vertices (Vectors) for a unit rounded rectangle (-0.5 to 0.5)
    centered at origin, in the plane defined by local_right/up.
    draw_radius is the calculated internal radius for drawing (expected 0.0 to 0.5).
    """
    half_w, half_h = 0.5, 0.5; center = Vector((0.0, 0.0, 0.0))
    effective_corner_radius = min(max(0.0, draw_radius), min(half_w, half_h) - 1e-4)
    if effective_corner_radius <= 1e-5:
        tr = center+(local_right*half_w)+(local_up*half_h); tl=center+(-local_right*half_w)+(local_up*half_h)
        return [tr,tl,center-tr,center-tl] # TR,TL,BL,BR (BL = -tr, BR = -tl)
    if segments_per_corner < 1: segments_per_corner = 1
    inner_w=half_w-effective_corner_radius; inner_h=half_h-effective_corner_radius
    c_tr=center+(local_right*inner_w)+(local_up*inner_h); c_tl=center+(-local_right*inner_w)+(local_up*inner_h)
    c_bl=center+(-local_right*inner_w)-(local_up*inner_h); c_br=center+(local_right*inner_w)-(local_up*inner_h)
    vertices = []; delta_angle = (math.pi/2.0)/segments_per_corner
    for i in range(segments_per_corner+1): angle=i*delta_angle; off=(local_right*math.cos(angle)+local_up*math.sin(angle))*effective_corner_radius; vertices.append(c_tr+off)
    for i in range(1,segments_per_corner+1): angle=(math.pi/2.0)+(i*delta_angle); off=(local_right*math.cos(angle)+local_up*math.sin(angle))*effective_corner_radius; vertices.append(c_tl+off)
    for i in range(1,segments_per_corner+1): angle=math.pi+(i*delta_angle); off=(local_right*math.cos(angle)+local_up*math.sin(angle))*effective_corner_radius; vertices.append(c_bl+off)
    for i in range(1,segments_per_corner+1): angle=(3*math.pi/2.0)+(i*delta_angle); off=(local_right*math.cos(angle)+local_up*math.sin(angle))*effective_corner_radius; vertices.append(c_br+off)
    return vertices

# --- Selection Helpers ---

def dist_point_to_segment_2d(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Calculate the min distance between a 2D point p and a 2D line segment (a, b)."""
    p_v=Vector(p); a_v=Vector(a); b_v=Vector(b); line_vec=b_v-a_v; point_vec=p_v-a_v
    line_len_sq = line_vec.length_squared
    if line_len_sq < 1e-9: return point_vec.length 
    t = point_vec.dot(line_vec)/line_len_sq
    if t < 0.0: closest = a_v
    elif t > 1.0: closest = b_v
    else: closest = a_v + t * line_vec
    return (p_v - closest).length

def get_blender_select_mouse() -> str:
    """
    Checks the user's keymap for the primary 3D View object selection button (simple click).
    Prioritizes exact property matches, falls back to simple modifier checks.
    Returns 'LEFTMOUSE' or 'RIGHTMOUSE', defaulting to 'LEFTMOUSE' only if search fails.
    """
    default_button = 'LEFTMOUSE'; found_button = None
    try:
        if not bpy.context or not bpy.context.window_manager: return default_button
        wm = bpy.context.window_manager; kc = wm.keyconfigs.user or wm.keyconfigs.addon or wm.keyconfigs.default
        if not kc: return default_button
        km = kc.keymaps.get('3D View')
        if not km: return default_button
        for kmi in km.keymap_items:
            if kmi.idname=='view3d.select' and kmi.value=='PRESS' and kmi.type in {'LEFTMOUSE','RIGHTMOUSE'}:
                props=kmi.properties
                is_strict=(not getattr(props,'extend',False) and not getattr(props,'deselect_all',False) and \
                           not getattr(props,'toggle',False) and not getattr(props,'center',False) and \
                           not getattr(props,'enumerate',False) and \
                           not kmi.shift and not kmi.ctrl and not kmi.alt and not kmi.oskey)
                if is_strict: found_button=kmi.type; break
        if not found_button:
             for kmi in km.keymap_items:
                 if kmi.idname=='view3d.select' and kmi.value=='PRESS' and \
                    kmi.type in {'LEFTMOUSE','RIGHTMOUSE'} and \
                    not kmi.shift and not kmi.ctrl and not kmi.alt and not kmi.oskey:
                     found_button=kmi.type; break
        if found_button: return found_button
    except Exception: pass 
    return default_button 

def find_and_set_new_active(context: bpy.types.Context, just_deselected_obj: bpy.types.Object):
    if not hasattr(context, 'view_layer') or context.view_layer.objects.active != just_deselected_obj: return
    selected_objects = context.selected_objects 
    new_active = selected_objects[0] if selected_objects else None
    try: context.view_layer.objects.active = new_active
    except (ReferenceError, RuntimeError): pass

# --- Hierarchy button (Up/Down) object name Helpers ---

def get_base_name_from_sdf_object(obj: bpy.types.Object) -> str:
    if not obj: return "SDF_Item"
    stored_base_name = obj.get("sdf_base_name")
    if stored_base_name and isinstance(stored_base_name, str) and stored_base_name.strip():
        return stored_base_name.strip()

    name_to_process = obj.name
    parts_suffix = name_to_process.rsplit('.', 1)
    if len(parts_suffix) == 2 and parts_suffix[1].isdigit() and len(parts_suffix[1]) == 3:
        name_to_process = parts_suffix[0]

    parts_prefix = name_to_process.split("_", 1)
    if len(parts_prefix) > 1 and len(parts_prefix[0]) == 3 and parts_prefix[0].isdigit():
        name_to_process = parts_prefix[1] if parts_prefix[1] else ""

    if not name_to_process.strip() or \
       (name_to_process == obj.name and (name_to_process.startswith("FF_") or name_to_process.lower().startswith("empty"))):
        if is_sdf_group(obj): return "Group"
        if is_sdf_canvas(obj): return "Canvas"
        if is_sdf_source(obj):
            sdf_type = obj.get("sdf_type")
            if sdf_type and isinstance(sdf_type, str): return sdf_type.capitalize()
    
    return name_to_process.strip() if name_to_process.strip() else "SDF_Item"

def update_linkers_globally(context: bpy.types.Context, old_target_name: str, new_target_name: str):
    """
    Iterates through all objects in the scene. If an object's SDF link target
    property matches old_target_name, it's updated to new_target_name.
    """
    if old_target_name == new_target_name: return
    if not hasattr(context, 'scene') or not context.scene: return

    updated_count = 0
    for obj_iterator in context.scene.objects:
        if constants.SDF_LINK_TARGET_NAME_PROP in obj_iterator:
            if obj_iterator.get(constants.SDF_LINK_TARGET_NAME_PROP) == old_target_name:
                try:
                    obj_iterator[constants.SDF_LINK_TARGET_NAME_PROP] = new_target_name
                    updated_count += 1
                except Exception as e: 
                    print(f"FieldForge WARN: Failed to update link on {obj_iterator.name} from '{old_target_name}' to '{new_target_name}': {e}")

def normalize_sibling_order_and_names(parent_obj: bpy.types.Object):
    """
    Normalizes 'sdf_processing_order' and names for relevant SDF children.
    Updates linkers globally if names change.
    """
    if not parent_obj: return
    context = bpy.context
    if not hasattr(context, 'scene') or not context.scene or not hasattr(context, 'view_layer'): return

    sdf_children_refs = []
    parent_is_canvas = is_sdf_canvas(parent_obj)

    for child in parent_obj.children:
        if not (child and child.visible_get(view_layer=context.view_layer)): continue
        is_relevant = False
        if parent_is_canvas:
            if is_sdf_source(child) and child.get("sdf_type") in constants._2D_SHAPE_TYPES: is_relevant = True
        elif is_sdf_source(child) or is_sdf_group(child) or is_sdf_canvas(child): is_relevant = True
        if is_relevant: sdf_children_refs.append(child)

    if not sdf_children_refs: return

    sdf_children_refs.sort(key=lambda c: (c.get("sdf_processing_order", float('inf')), c.name))

    # Pass 1: Set new 'sdf_processing_order' and ensure 'sdf_base_name' is populated.
    for i, child_obj in enumerate(sdf_children_refs):
        child_obj["sdf_processing_order"] = i * 10
        current_base = child_obj.get("sdf_base_name")
        if not current_base or not isinstance(current_base, str) or not current_base.strip():
            child_obj["sdf_base_name"] = get_base_name_from_sdf_object(child_obj)
        
        base_name_val = child_obj.get("sdf_base_name", "SDF_Item")
        new_base_name = base_name_val
        if is_sdf_group(child_obj) and base_name_val in ["SDF_Item", "FF_Group", "Empty", "Group_temp"]: new_base_name = "Group"
        elif is_sdf_canvas(child_obj) and base_name_val in ["SDF_Item", "FF_Canvas", "Empty", "Canvas_temp"]: new_base_name = "Canvas"
        elif is_sdf_source(child_obj):
            sdf_type = child_obj.get("sdf_type")
            # If base name is generic or a temp name, use type
            if base_name_val in ["SDF_Item", "Empty"] or base_name_val.endswith("_temp"):
                if sdf_type and isinstance(sdf_type, str): new_base_name = sdf_type.capitalize()
            elif base_name_val.startswith("FF_") and sdf_type and isinstance(sdf_type, str): # e.g. FF_Cube -> Cube
                 if base_name_val[3:].lower() == sdf_type.lower(): new_base_name = sdf_type.capitalize()


        if child_obj.get("sdf_base_name") != new_base_name : child_obj["sdf_base_name"] = new_base_name


    # Pass 2: Rename objects and log renames.
    renamed_info = []
    for child_obj in sdf_children_refs:
        old_name = child_obj.name
        base_name = child_obj.get("sdf_base_name", "SDF_Item")
        order = child_obj.get("sdf_processing_order", 0)
        desired_name = f"{order:03d}_{base_name}"
        
        if child_obj.name != desired_name:
            try: child_obj.name = desired_name
            except Exception as e:
                print(f"FieldForge WARN: Could not rename '{old_name}' to '{desired_name}': {e}")
                continue 
        
        actual_new_name = child_obj.name
        if old_name != actual_new_name:
            renamed_info.append((old_name, actual_new_name))

    # Pass 3: Update global linkers.
    if renamed_info:
        for old_name_log, actual_new_name_log in renamed_info:
            update_linkers_globally(context, old_name_log, actual_new_name_log)

    if hasattr(context, 'screen') and context.screen:
        for area in context.screen.areas:
            if area.type == 'OUTLINER': area.tag_redraw(); break