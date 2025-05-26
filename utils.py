# FieldForge/utils.py

"""
General utility and helper functions for the FieldForge addon.
"""

import bpy
import math
import uuid
from mathutils import Vector, Matrix

# Use relative import to get constants from the parent directory's constants.py
from . import constants


# --- Globals (Potentially set by __init__.py if needed) ---
# These might be set after libfive is loaded in __init__.py if other utils need them.
# Alternatively, functions requiring lf/ffi can import them directly if available.
lf = None
ffi = None

# --- Blender Object/Hierarchy Helpers ---

def get_all_bounds_objects(context: bpy.types.Context):
    """ Generator yielding all SDF Bounds objects in the current scene """
    for obj in context.scene.objects:
        # Use get() for safe access to custom property
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

def is_sdf_source(obj: bpy.types.Object) -> bool:
    """ Checks if an object is configured as an SDF source Empty """
    # Check object exists, is an EMPTY type, and has the marker property
    return obj and obj.type == 'EMPTY' and obj.get(constants.SDF_PROPERTY_MARKER, False)

def is_valid_2d_loft_source(obj: bpy.types.Object) -> bool:
    """Checks if an object is an SDF source and is a 2D type eligible for lofting."""
    if not is_sdf_source(obj):
        return False
    # Check the sdf_type property associated with 2D shapes
    sdf_type = obj.get("sdf_type", "")
    # Expand this set if more 2D base shapes are added later
    return sdf_type in {"circle", "ring", "polygon"}


# --- Settings and Property Helpers ---

def get_bounds_setting(bounds_obj: bpy.types.Object, setting_key: str):
    """
    Safely retrieves a setting from a bounds object's custom properties,
    falling back to the addon's default settings if the property is missing.
    """
    if not bounds_obj or not setting_key:
        # Return default if object is invalid or key is empty
        return constants.DEFAULT_SETTINGS.get(setting_key)

    # Use .get(key, default) directly on the object (accessing custom props)
    # The second argument to .get() is the fallback value if the key doesn't exist.
    return bounds_obj.get(setting_key, constants.DEFAULT_SETTINGS.get(setting_key))


# --- Comparison Helpers (for State Caching) ---

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
        val2 = dict2.get(key) # Use get() for safety in case keys mismatch (shouldn't happen due to check above)

        # Type-specific comparisons with tolerance
        if isinstance(val1, float):
            if not isinstance(val2, (float, int)) or abs(val1 - val2) > tolerance: return False
        elif isinstance(val1, Matrix):
            if not compare_matrices(val1, val2, tolerance): return False
        elif isinstance(val1, Vector):
             if not compare_vectors(val1, val2, tolerance): return False
        # Basic type comparisons (int, bool, str, NoneType)
        elif val1 != val2:
            # This covers int, bool, str comparisons and None vs not-None
            return False
    return True


# --- Drawing Helpers ---
# (Moved geometry creation helpers here)

def create_circle_vertices(center: Vector, right: Vector, up: Vector, radius: float, segments: int) -> list[Vector]:
    """ Generates world-space vertices for a circle defined by center, orthogonal axes, radius, and segments. """
    if segments < 3: return []
    vertices = []
    if radius <= 1e-6: # Avoid issues with zero radius
         return [center] * segments # Return points at center if radius is tiny
    try:
        for i in range(segments):
            angle = (i / segments) * 2 * math.pi
            offset = (right * math.cos(angle) + up * math.sin(angle)) * radius
            vertices.append(center + offset)
    except Exception as e:
        print(f"Error in create_circle_vertices: {e}")
        return []
    return vertices

def create_rectangle_vertices(center: Vector, right: Vector, up: Vector, width: float, height: float) -> list[Vector]:
    """Generates world-space vertices for a rectangle (TR, TL, BL, BR order)."""
    half_w = max(0.0, width / 2.0); half_h = max(0.0, height / 2.0)
    tr = (right * half_w) + (up * half_h)
    tl = (-right * half_w) + (up * half_h)
    bl = (-right * half_w) - (up * half_h)
    br = (right * half_w) - (up * half_h)
    return [center + tr, center + tl, center + bl, center + br]

def create_unit_circle_vertices_xy(segments: int) -> list[tuple[float, float, float]]:
    """ Generates local vertices (tuples) for a unit circle (radius 0.5) in the XY plane. """
    if segments < 3: return []
    vertices = []
    radius = 0.5
    for i in range(segments):
        angle = (i / segments) * 2 * math.pi
        vertices.append( (math.cos(angle) * radius, math.sin(angle) * radius, 0.0) )
    return vertices

def create_unit_polygon_vertices_xy(segments: int) -> list[tuple[float, float, float]]:
    """ Generates local vertices (tuples) for a regular unit polygon (inscribed radius 0.5) in the XY plane. """
    if segments < 3: return []
    vertices = []; radius = 0.5
    angle_offset = -math.pi / 2.0 # Vertex down default
    if segments % 2 == 0: angle_offset += (math.pi / segments) # Flat bottom for even sides
    for i in range(segments):
        angle = angle_offset + (i / segments) * 2 * math.pi
        vertices.append( (math.cos(angle) * radius, math.sin(angle) * radius, 0.0) )
    return vertices

def create_unit_cylinder_cap_vertices(segments: int) -> tuple[list, list]:
    """ Generates local vertices (tuples) for top/bottom caps of a unit cylinder (r=0.5, h=1.0, centered). """
    top_verts = []; bot_verts = []; radius = 0.5; half_height = 0.5
    if segments >= 3:
        for i in range(segments):
            angle = (i / segments) * 2 * math.pi
            x = math.cos(angle) * radius; y = math.sin(angle) * radius
            top_verts.append( (x, y, half_height) )
            bot_verts.append( (x, y, -half_height) )
    return top_verts, bot_verts

def create_unit_rounded_rectangle_plane(local_right: Vector, local_up: Vector, draw_radius: float, segments_per_corner: int) -> list[Vector]:
    """
    Generates local vertices (Vectors) for a unit rounded rectangle (-0.5 to 0.5)
    centered at origin, in the plane defined by local_right/up.
    draw_radius is the calculated internal radius for drawing (expected 0.0 to 0.5).
    """
    half_w, half_h = 0.5, 0.5
    center = Vector((0.0, 0.0, 0.0))
    radius = max(0.0, min(draw_radius, half_w))
    effective_corner_radius = min(radius, half_w - 1e-4)

    # If radius is effectively zero, return sharp corners
    if radius <= 1e-5:
        tr = center + (local_right * half_w) + (local_up * half_h)
        tl = center + (-local_right * half_w) + (local_up * half_h)
        bl = center + (-local_right * half_w) - (local_up * half_h)
        br = center + (local_right * half_w) - (local_up * half_h)
        return [tr, tl, bl, br]

    if segments_per_corner < 1: segments_per_corner = 1
    inner_w = half_w - effective_corner_radius
    inner_h = half_h - effective_corner_radius
    # Corner centers
    center_tr = center + (local_right * inner_w) + (local_up * inner_h)
    center_tl = center + (-local_right * inner_w) + (local_up * inner_h)
    center_bl = center + (-local_right * inner_w) - (local_up * inner_h)
    center_br = center + (local_right * inner_w) - (local_up * inner_h)
    vertices = []
    delta_angle = (math.pi / 2.0) / segments_per_corner
    arc_radius = radius
    # TR corner (0 to pi/2)
    for i in range(segments_per_corner + 1): angle = i * delta_angle; offset = (local_right * math.cos(angle) + local_up * math.sin(angle)) * arc_radius; vertices.append(center_tr + offset)
    # TL corner (pi/2 to pi)
    for i in range(1, segments_per_corner + 1): angle = (math.pi / 2.0) + (i * delta_angle); offset = (local_right * math.cos(angle) + local_up * math.sin(angle)) * arc_radius; vertices.append(center_tl + offset)
    # BL corner (pi to 3pi/2)
    for i in range(1, segments_per_corner + 1): angle = math.pi + (i * delta_angle); offset = (local_right * math.cos(angle) + local_up * math.sin(angle)) * arc_radius; vertices.append(center_bl + offset)
    # BR corner (3pi/2 to 2pi)
    for i in range(1, segments_per_corner + 1): angle = (3 * math.pi / 2.0) + (i * delta_angle); offset = (local_right * math.cos(angle) + local_up * math.sin(angle)) * arc_radius; vertices.append(center_br + offset)

    return vertices

# --- Selection Helpers ---

def dist_point_to_segment_2d(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Calculate the min distance between a 2D point p and a 2D line segment (a, b)."""
    p_v = Vector(p); a_v = Vector(a); b_v = Vector(b)
    ab = b_v - a_v; ap = p_v - a_v
    ab_mag_sq = ab.length_squared
    if ab_mag_sq < 1e-9: return ap.length # Segment is a point
    t = ap.dot(ab) / ab_mag_sq # Project p onto line ab
    # Find closest point on segment
    if t < 0.0: proj = a_v
    elif t > 1.0: proj = b_v
    else: proj = a_v + t * ab
    return (p_v - proj).length # Distance from p to projection

def get_blender_select_mouse_bak() -> str:
    """ Checks user keymap for primary 3D View select button. """
    # (Implementation from previous answer - finds 'LEFTMOUSE' or 'RIGHTMOUSE')
    default_button = 'LEFTMOUSE'; found_button = None
    try:
        if not bpy.context or not bpy.context.window_manager: return default_button
        wm = bpy.context.window_manager; kc = wm.keyconfigs.user or wm.keyconfigs.addon or wm.keyconfigs.default
        if not kc: return default_button; km = kc.keymaps.get('3D View')
        if not km: return default_button
        for kmi in km.keymap_items: # Strict Pass
            if kmi.idname == 'view3d.select' and kmi.value == 'PRESS' and kmi.type in {'LEFTMOUSE', 'RIGHTMOUSE'}:
                props = kmi.properties
                is_strict = (getattr(props, 'extend', False)==False and getattr(props, 'deselect_all', False)==False and getattr(props, 'toggle', False)==False and getattr(props, 'center', False)==False and getattr(props, 'enumerate', False)==False and not kmi.shift and not kmi.ctrl and not kmi.alt and not kmi.oskey)
                if is_strict: found_button = kmi.type; break
        if not found_button: # Relaxed Pass
             for kmi in km.keymap_items:
                 if kmi.idname == 'view3d.select' and kmi.value == 'PRESS' and kmi.type in {'LEFTMOUSE', 'RIGHTMOUSE'} and not kmi.shift and not kmi.ctrl and not kmi.alt and not kmi.oskey:
                     found_button = kmi.type; break
        if found_button: return found_button
    except Exception: pass # Ignore errors during lookup
    return default_button # Fallback
def get_blender_select_mouse() -> str:
    """
    Checks the user's keymap for the primary 3D View object selection button (simple click).
    Prioritizes exact property matches, falls back to simple modifier checks.
    Returns 'LEFTMOUSE' or 'RIGHTMOUSE', defaulting to 'LEFTMOUSE' only if search fails.
    """
    default_button = 'LEFTMOUSE' # Default ONLY if search logic fails completely
    found_button = None

    try:
        if not bpy.context or not bpy.context.window_manager:
            # print("FF Keymap WARN: No bpy.context or window_manager available.")
            return default_button

        wm = bpy.context.window_manager
        # Ensure we get the active config, falling back correctly
        kc = wm.keyconfigs.user or wm.keyconfigs.addon or wm.keyconfigs.default
        if not kc: # Should not happen, but safeguard
             # print("FF Keymap ERROR: Could not determine keyconfig.")
             return default_button
        km = kc.keymaps.get('3D View')
        if not km:
            # print("FF Keymap WARN: '3D View' keymap not found.")
            return default_button

        # --- First Pass: Look for the STRICT match based on properties ---
        # This prioritizes the keymap item that exactly matches the behavior
        # of a default single-click select (no toggling, extending, etc.)
        for kmi in km.keymap_items:
            # Filter early for relevant types
            if kmi.idname == 'view3d.select' and kmi.value == 'PRESS' and kmi.type in {'LEFTMOUSE', 'RIGHTMOUSE'}:
                props = kmi.properties
                # Check properties typical of a basic, non-modifier click
                is_primary_select_strict = (
                    getattr(props, 'extend', False) == False and
                    getattr(props, 'deselect_all', False) == False and # Relax this one slightly? No, basic click usually doesn't deselect all.
                    getattr(props, 'toggle', False) == False and
                    getattr(props, 'center', False) == False and
                    getattr(props, 'enumerate', False) == False and
                    # Check modifiers on the keymap item itself are OFF
                    not kmi.shift and not kmi.ctrl and not kmi.alt and not kmi.oskey
                )

                if is_primary_select_strict:
                    # Store the first strict match found and stop this pass
                    found_button = kmi.type
                    # print(f"FF Keymap DBG: Found STRICT match: {found_button}") # DEBUG
                    break

        # --- Second Pass: If no strict match, find ANY mouse click without modifiers ---
        # This catches cases where properties might be slightly different (like deselect_all=True)
        # but the user interaction (simple click, no Shift/Ctrl/Alt) is correct.
        if not found_button:
            for kmi in km.keymap_items:
                 if kmi.idname == 'view3d.select' and \
                    kmi.value == 'PRESS' and \
                    kmi.type in {'LEFTMOUSE', 'RIGHTMOUSE'} and \
                    not kmi.shift and not kmi.ctrl and not kmi.alt and not kmi.oskey: # Check modifiers are off

                     # Take the first one found in this relaxed pass
                     found_button = kmi.type
                     # print(f"FF Keymap DBG: Found RELAXED match: {found_button}") # DEBUG
                     break # Stop after finding the first relaxed match

        # --- Final Decision ---
        if found_button:
            # print(f"FF Keymap INFO: Using detected select button: {found_button}") # INFO
            return found_button
        else:
            # This case means even the relaxed search failed.
            print(f"FF Keymap WARN: Could not detect primary select mouse button ('view3d.select' without modifiers). Using default: {default_button}")

    except AttributeError as ae:
         # print(f"FieldForge WARN: Couldn't fully inspect keymaps (AttributeError): {ae}")
         traceback.print_exc() # Optional: Print stack trace for attribute errors
    except Exception as e:
        # print(f"FieldForge WARN: Error querying keymap: {type(e).__name__}: {e}")
        traceback.print_exc() # Print stack trace for unexpected errors

    # Return default ONLY if search failed or exception occurred
    # print(f"FF Keymap INFO: Returning default select button due to fallback: {default_button}") # DEBUG
    return default_button

def find_and_set_new_active(context: bpy.types.Context, just_deselected_obj: bpy.types.Object):
    """ Finds new active object after deselection if necessary. """
    if context.view_layer.objects.active != just_deselected_obj: return
    selected_objects = context.selected_objects # This list is already updated
    new_active = selected_objects[0] if selected_objects else None
    context.view_layer.objects.active = new_active

def get_base_name_from_sdf_object(obj: bpy.types.Object) -> str:
    """
    Attempts to get a base name for an SDF object, stripping existing numerical prefixes
    and Blender's .001 duplicate suffixes.
    It can also use a stored 'sdf_base_name' property if available.
    """
    if "sdf_base_name" in obj and obj["sdf_base_name"]: # Check if not empty
        return obj["sdf_base_name"]

    name_to_process = obj.name
    
    parts_suffix = name_to_process.rsplit('.', 1)
    if len(parts_suffix) == 2 and parts_suffix[1].isdigit() and len(parts_suffix[1]) == 3:
        name_to_process = parts_suffix[0]

    parts_prefix = name_to_process.split("_", 1)
    if len(parts_prefix) > 1 and parts_prefix[0].isdigit() and len(parts_prefix[0]) == 3:
        if parts_prefix[1]:
             return parts_prefix[1]
        return name_to_process
    return name_to_process


def normalize_sibling_order_and_names(parent_obj: bpy.types.Object):
    """
    Normalizes the 'sdf_processing_order' property and names for all visible
    SDF source children of a given parent object.
    Lower order numbers process first.
    Names are updated to 'NNN_BaseName'.
    """
    if not parent_obj:
        return

    context = bpy.context 

    sdf_children = []
    for child in parent_obj.children:
        if child and is_sdf_source(child) and child.visible_get(view_layer=context.view_layer):
            sdf_children.append(child)

    if not sdf_children:
        return

    def sort_key_for_normalization(c):
        return (c.get("sdf_processing_order", float('inf')), c.name)

    sdf_children.sort(key=sort_key_for_normalization)

    # Pass 1: Rename to temporary unique names to avoid clashes
    temp_name_map = {}
    for i, child_obj in enumerate(sdf_children):
        original_name = child_obj.name
        temp_name = f"_TEMP_FF_{uuid.uuid4().hex[:12]}" 
        try:
            child_obj.name = temp_name
            temp_name_map[temp_name] = child_obj
        except Exception as e:
            print(f"FieldForge WARN: Could not rename {original_name} to temp name {temp_name}: {e}")
            temp_name_map[original_name] = child_obj

    # Pass 2: Assign final order and names
    # We iterate based on the original sdf_children list which is correctly sorted
    # but retrieve objects via the temp_name_map to ensure we have the right references
    # if Blender did something unexpected during temp renaming.
    final_renamed_children = []
    for i, original_sorted_child_ref in enumerate(sdf_children):
        child_obj_found = None
        for temp_n, obj_ref in temp_name_map.items():
            if obj_ref == original_sorted_child_ref :
                try: 
                    _ = bpy.data.objects[temp_n].name
                    child_obj_found = bpy.data.objects[temp_n]
                except KeyError:
                    try:
                         _ = original_sorted_child_ref.name 
                         child_obj_found = original_sorted_child_ref
                    except ReferenceError:
                        print(f"FieldForge WARN: Object {original_sorted_child_ref} seems to be gone before final rename.")
                        continue
                break

        if not child_obj_found:
            try:
                child_obj_found = bpy.data.objects[original_sorted_child_ref.name]
            except (KeyError, ReferenceError):
                print(f"FieldForge WARN: Could not re-find object for final rename: {original_sorted_child_ref.name}")
                continue

        new_order = i * 10
        child_obj_found["sdf_processing_order"] = new_order

        if "sdf_base_name" not in child_obj_found or not child_obj_found["sdf_base_name"]:
            derived_base_name = get_base_name_from_sdf_object(child_obj_found) 
            child_obj_found["sdf_base_name"] = derived_base_name

        base_name_to_use = child_obj_found.get("sdf_base_name", "SDF_Object") 

        new_name_candidate = f"{new_order:03d}_{base_name_to_use}"
        final_new_name = new_name_candidate

        current_name_idx = 0
        while final_new_name in bpy.data.objects and bpy.data.objects[final_new_name] != child_obj_found:
            current_name_idx += 1
            final_new_name = f"{new_name_candidate}.{current_name_idx:03d}"

        if child_obj_found.name != final_new_name: 
            try:
                child_obj_found.name = final_new_name
            except Exception as e:
                print(f"FieldForge WARN: Could not assign final name {final_new_name} to {child_obj_found.name} (was temp): {e}")
        final_renamed_children.append(child_obj_found)

    if context.screen: 
        for area in context.screen.areas:
            if area.type == 'OUTLINER':
                area.tag_redraw()
                break