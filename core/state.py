"""
Handles gathering the current state of SDF objects within a hierarchy
and comparing it to previously cached states for change detection.
"""

import bpy
from mathutils import Vector, Matrix

from .. import constants
from .. import utils


def get_current_sdf_state(context: bpy.types.Context, bounds_obj: bpy.types.Object) -> dict | None:
    """
    Gathers the current relevant state for a specific Bounds hierarchy.

    Includes bounds settings, bounds transform, and details (transform, properties)
    of all *visible* SDF Source objects within that hierarchy.

    Returns a dictionary representing the state, or None if bounds_obj is invalid.
    """
    if not bounds_obj or not bounds_obj.get(constants.SDF_BOUNDS_MARKER, False):
        print("FieldForge WARN (get_state): Invalid or non-bounds object passed.")
        return None

    bounds_name = bounds_obj.name

    current_state = {
        'bounds_name': bounds_name,
        'scene_settings': {}, # Settings specific to this bounds object
        'bounds_matrix': bounds_obj.matrix_world.copy(),
        'source_objects': {}, # Dictionary: {obj_name: {matrix: ..., props: {...}}}
        'group_objects': {}
    }

    # Read settings from the bounds object itself into the state dictionary
    # Use utils.get_bounds_setting to handle defaults correctly
    for key in constants.DEFAULT_SETTINGS.keys():
        current_state['scene_settings'][key] = utils.get_bounds_setting(bounds_obj, key)

    # Traverse hierarchy below this specific bounds object to find visible sources
    # Use a queue for breadth-first or depth-first traversal
    queue = [bounds_obj]
    # Keep track of visited objects *within this specific hierarchy traversal*
    # to avoid issues with objects parented under multiple relevant paths (though unlikely).
    visited_in_hierarchy = {bounds_name}

    while queue:
        parent_obj = queue.pop(0) # Depth-first: use pop(); Breadth-first: use pop(0)

        # Iterate through children safely
        children = list(parent_obj.children) # Copy children list in case hierarchy changes mid-iteration
        for child_obj in children:
            if not child_obj: continue # Child might be None temporarily
            child_name = child_obj.name
            if child_name in visited_in_hierarchy: continue # Already processed this node in this traversal
            visited_in_hierarchy.add(child_name)

            # Check if the object *still exists* in the scene's object collection
            # (it could have been deleted since the .children list was accessed)
            actual_child_obj = context.scene.objects.get(child_name)
            if not actual_child_obj:
                continue # Skip object that no longer exists

            # --- Check visibility using the context's view layer ---
            # Use visible_get() which respects parent visibility, layer visibility etc.
            is_visible = actual_child_obj.visible_get(view_layer=context.view_layer)

            # Process only if it's an SDF source AND visible in the viewport
            if utils.is_sdf_source(actual_child_obj) and is_visible:
                # Gather state for this visible source object
                sdf_type = actual_child_obj.get("sdf_type") # Get type early

                # Build dictionary of relevant properties to track for changes
                props_to_track = {
                    # Core props
                    'sdf_type': sdf_type,
                    'sdf_child_blend_factor': actual_child_obj.get("sdf_child_blend_factor", constants.DEFAULT_SOURCE_SETTINGS["sdf_child_blend_factor"]),
                    'sdf_csg_operation': actual_child_obj.get("sdf_csg_operation", constants.DEFAULT_SOURCE_SETTINGS["sdf_csg_operation"]),
                    # Interaction Modifiers
                    'sdf_use_clearance': actual_child_obj.get("sdf_use_clearance", False),
                    'sdf_clearance_offset': actual_child_obj.get("sdf_clearance_offset", constants.DEFAULT_SOURCE_SETTINGS["sdf_clearance_offset"]),
                    'sdf_clearance_keep_original': actual_child_obj.get("sdf_clearance_keep_original", constants.DEFAULT_SOURCE_SETTINGS["sdf_clearance_keep_original"]),
                    'sdf_use_morph': actual_child_obj.get("sdf_use_morph", False),
                    'sdf_morph_factor': actual_child_obj.get("sdf_morph_factor", constants.DEFAULT_SOURCE_SETTINGS["sdf_morph_factor"]),
                    'sdf_use_loft': actual_child_obj.get("sdf_use_loft", False),
                    # Shell Modifier
                    'sdf_use_shell': actual_child_obj.get("sdf_use_shell", False),
                    'sdf_shell_offset': actual_child_obj.get("sdf_shell_offset", constants.DEFAULT_SOURCE_SETTINGS["sdf_shell_offset"]),
                    # Array Modifier (Main Mode)
                    'sdf_main_array_mode': actual_child_obj.get("sdf_main_array_mode", 'NONE'),
                    # Linear Array Props (Only relevant if mode is LINEAR, but get anyway for simplicity)
                    'sdf_array_active_x': actual_child_obj.get("sdf_array_active_x", False),
                    'sdf_array_active_y': actual_child_obj.get("sdf_array_active_y", False),
                    'sdf_array_active_z': actual_child_obj.get("sdf_array_active_z", False),
                    'sdf_array_delta_x': actual_child_obj.get("sdf_array_delta_x", 1.0),
                    'sdf_array_delta_y': actual_child_obj.get("sdf_array_delta_y", 1.0),
                    'sdf_array_delta_z': actual_child_obj.get("sdf_array_delta_z", 1.0),
                    'sdf_array_count_x': actual_child_obj.get("sdf_array_count_x", 2),
                    'sdf_array_count_y': actual_child_obj.get("sdf_array_count_y", 2),
                    'sdf_array_count_z': actual_child_obj.get("sdf_array_count_z", 2),
                    # Radial Array Props (Only relevant if mode is RADIAL)
                    'sdf_radial_count': actual_child_obj.get("sdf_radial_count", 6),
                    'sdf_radial_center': tuple(actual_child_obj.get("sdf_radial_center", (0.0, 0.0))),
                    'sdf_radial_array_center_on_origin': actual_child_obj.get("sdf_array_center_on_origin", constants.DEFAULT_SOURCE_SETTINGS["sdf_array_center_on_origin"]),
                    # --- Shape-specific props ---
                    # Use conditional get based on sdf_type to keep state clean
                    'sdf_text_string': actual_child_obj.get("sdf_text_string") if sdf_type == "text" else None,
                    'sdf_round_radius': actual_child_obj.get("sdf_round_radius") if sdf_type == "rounded_box" else None,
                    'sdf_extrusion_depth': actual_child_obj.get("sdf_extrusion_depth") if sdf_type in {"circle", "ring", "polygon", "text"} else None,
                    'sdf_inner_radius': actual_child_obj.get("sdf_inner_radius") if sdf_type == "ring" else None,
                    'sdf_sides': actual_child_obj.get("sdf_sides") if sdf_type == "polygon" else None,
                    'sdf_torus_major_radius': actual_child_obj.get("sdf_torus_major_radius") if sdf_type == "torus" else None,
                    'sdf_torus_minor_radius': actual_child_obj.get("sdf_torus_minor_radius") if sdf_type == "torus" else None,
                }
                # Remove None values from props_to_track for cleaner comparison
                props_to_track = {k: v for k, v in props_to_track.items() if v is not None}

                # Store state for this object
                obj_state = {
                    'matrix': actual_child_obj.matrix_world.copy(),
                    'props': props_to_track
                }
                current_state['source_objects'][child_name] = obj_state

                # Add source object to queue to check its children as well
                queue.append(actual_child_obj)

            # Process SDF Group Objects
            elif utils.is_sdf_group(actual_child_obj) and is_visible:
                props_to_track_group = {
                    'sdf_child_blend_factor': actual_child_obj.get("sdf_child_blend_factor", constants.DEFAULT_BLEND_GROUP_SETTINGS["sdf_child_blend_factor"]),
                }
                group_obj_state = {
                    'matrix': actual_child_obj.matrix_world.copy(),
                    'props': props_to_track_group
                }
                current_state['group_objects'][child_name] = group_obj_state
                queue.append(actual_child_obj)

            # If it's not a source object but might have children (e.g., a regular Empty group node),
            # still add it to the queue to traverse further down, but only if it's visible.
            elif is_visible and actual_child_obj.children:
                 queue.append(actual_child_obj)
    return current_state


def has_state_changed(current_state: dict, cached_state: dict | None) -> bool:
    """
    Compares the current state to the cached state for a specific bounds object.
    Uses helper functions for tolerant comparison of matrices, vectors, floats.

    Returns True if a relevant change is detected, False otherwise.
    """
    if not current_state:
        return False # Cannot compare if no current state
    if not cached_state:
        return True # No cache exists, so state has effectively changed

    # 1. Compare Settings stored on the bounds object
    # Use utils.compare_dicts for tolerance
    if not utils.compare_dicts(current_state.get('scene_settings'), cached_state.get('scene_settings')):
        return True

    # 2. Compare Bounds Matrix
    # Use utils.compare_matrices for tolerance
    if not utils.compare_matrices(current_state.get('bounds_matrix'), cached_state.get('bounds_matrix')):
        return True

    # 3. Compare the set of active source objects (keys of the dict)
    current_source_names = set(current_state.get('source_objects', {}).keys())
    cached_source_names = set(cached_state.get('source_objects', {}).keys())
    if current_source_names != cached_source_names:
        return True

    # 4. Compare individual source object states (matrix and properties)
    current_sources = current_state.get('source_objects', {})
    cached_sources = cached_state.get('source_objects', {})
    for obj_name, current_obj_state in current_sources.items():
        cached_obj_state = cached_sources.get(obj_name)
        # This check should be redundant due to key set check, but safe backup
        if not cached_obj_state:
             return True

        # Compare matrix
        if not utils.compare_matrices(current_obj_state.get('matrix'), cached_obj_state.get('matrix')):
            return True
        # Compare properties dict
        if not utils.compare_dicts(current_obj_state.get('props'), cached_obj_state.get('props')):
            return True

    current_group_names = set(current_state.get('group_objects', {}).keys())
    cached_group_names = set(cached_state.get('group_objects', {}).keys())
    if current_group_names != cached_group_names:
        return True
    current_groups = current_state.get('group_objects', {})
    cached_groups = cached_state.get('group_objects', {})
    for obj_name, current_group_obj_state in current_groups.items():
        cached_group_obj_state = cached_groups.get(obj_name)
        if not cached_group_obj_state: return True
        if not utils.compare_matrices(current_group_obj_state.get('matrix'), cached_group_obj_state.get('matrix')):
            return True
        if not utils.compare_dicts(current_group_obj_state.get('props'), cached_group_obj_state.get('props')):
            return True
            
    return False