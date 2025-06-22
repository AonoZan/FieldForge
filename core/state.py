"""
Handles gathering the current state of SDF objects within a hierarchy
and comparing it to previously cached states for change detection.
"""

import bpy
from mathutils import Vector, Matrix

from .. import constants
from .. import utils

_link_dependents_cache = {}
_reverse_link_cache = {}

def _update_linker_caches(linker_obj: bpy.types.Object, new_target_name: str | None, linker_parent_bounds_name: str | None):
    """Manages the _link_dependents_cache and _reverse_link_cache."""
    global _link_dependents_cache, _reverse_link_cache
    
    linker_name = linker_obj.name

    old_target_name_for_linker = _reverse_link_cache.pop(linker_name, None)
    if old_target_name_for_linker and old_target_name_for_linker in _link_dependents_cache:
        if linker_parent_bounds_name in _link_dependents_cache[old_target_name_for_linker]:
            _link_dependents_cache[old_target_name_for_linker].remove(linker_parent_bounds_name)
            if not _link_dependents_cache[old_target_name_for_linker]:
                del _link_dependents_cache[old_target_name_for_linker]

    if new_target_name and linker_parent_bounds_name:
        _link_dependents_cache.setdefault(new_target_name, set()).add(linker_parent_bounds_name)
        _reverse_link_cache[linker_name] = new_target_name
    elif linker_name in _reverse_link_cache:
        del _reverse_link_cache[linker_name]

def register_link_dependency(linker_obj: bpy.types.Object, effective_target_obj: bpy.types.Object | None, linker_parent_bounds: bpy.types.Object | None):
    if not linker_obj:
        return

    linker_parent_bounds_name = linker_parent_bounds.name if linker_parent_bounds else None
    
    current_link_target_prop_val = linker_obj.get(constants.SDF_LINK_TARGET_NAME_PROP, "")

    if effective_target_obj and effective_target_obj != linker_obj and current_link_target_prop_val == effective_target_obj.name:
        _update_linker_caches(linker_obj, effective_target_obj.name, linker_parent_bounds_name)
    else:
        _update_linker_caches(linker_obj, None, linker_parent_bounds_name)

def get_dependent_bounds_for_linked_object(obj_name: str) -> set:
    """Public accessor for update_manager to get dependent bounds names."""
    return _link_dependents_cache.get(obj_name, set())

def clear_link_caches():
    """Public function to be called from update_manager on cleanup."""
    global _link_dependents_cache, _reverse_link_cache
    _link_dependents_cache.clear()
    _reverse_link_cache.clear()


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
        'canvas_objects': {},
        'group_objects': {}
    }

    # Read settings from the bounds object itself into the state dictionary
    # Use utils.get_bounds_setting to handle defaults correctly
    for key, default_val in constants.DEFAULT_SETTINGS.items():
        # Check if this key is the link target prop itself to avoid recursion if bounds links to itself for settings
        if key == constants.SDF_LINK_TARGET_NAME_PROP:
            current_state['scene_settings'][key] = bounds_obj.get(key, default_val)
        else:
            # For other settings, respect linking if bounds_obj were to link its settings
            current_state['scene_settings'][key] = utils.get_sdf_param(bounds_obj, key, default_val)

    # Traverse hierarchy below this specific bounds object to find visible sources
    # Use a queue for breadth-first or depth-first traversal
    queue = [bounds_obj]
    # Keep track of visited objects *within this specific hierarchy traversal*
    # to avoid issues with objects parented under multiple relevant paths (though unlikely).
    visited_in_hierarchy = {bounds_name}

    while queue:
        parent_obj_iterator = queue.pop(0)
        children = list(parent_obj_iterator.children)
        for child_obj in children:
            if not child_obj: continue # Child might be None temporarily
            child_name = child_obj.name
            if child_name in visited_in_hierarchy: continue # Already processed this node in this traversal
            visited_in_hierarchy.add(child_name)
            actual_child_obj = context.scene.objects.get(child_obj.name)
            if not actual_child_obj: continue
            is_visible = actual_child_obj.visible_get(view_layer=context.view_layer)
            child_name = actual_child_obj.name

            if utils.is_sdf_source(actual_child_obj) or utils.is_sdf_group(actual_child_obj) or utils.is_sdf_canvas(actual_child_obj):
                effective_target_for_child = utils.get_effective_sdf_object(actual_child_obj)
                register_link_dependency(actual_child_obj, effective_target_for_child, bounds_obj)

            if utils.is_sdf_source(actual_child_obj) and is_visible:
                props_to_track = {}
                props_to_track[constants.SDF_LINK_TARGET_NAME_PROP] = actual_child_obj.get(constants.SDF_LINK_TARGET_NAME_PROP, "") # Always from actual_child_obj
                
                for key, default_val in constants.DEFAULT_SOURCE_SETTINGS.items():
                    if key != constants.SDF_LINK_TARGET_NAME_PROP: # Don't get link target from target
                        props_to_track[key] = utils.get_sdf_param(actual_child_obj, key, default_val)
                
                props_to_track = {k: v for k, v in props_to_track.items() if v is not None or k == constants.SDF_LINK_TARGET_NAME_PROP}


                obj_state = {
                    'matrix': actual_child_obj.matrix_world.copy(),
                    'props': props_to_track 
                }
                current_state['source_objects'][child_name] = obj_state
                queue.append(actual_child_obj)

            elif utils.is_sdf_group(actual_child_obj) and is_visible:
                props_to_track_group = {}
                props_to_track_group[constants.SDF_LINK_TARGET_NAME_PROP] = actual_child_obj.get(constants.SDF_LINK_TARGET_NAME_PROP, "")
                for key, default_val in constants.DEFAULT_GROUP_SETTINGS.items():
                     if key != constants.SDF_LINK_TARGET_NAME_PROP:
                        props_to_track_group[key] = utils.get_sdf_param(actual_child_obj, key, default_val)
                group_obj_state = {
                    'matrix': actual_child_obj.matrix_world.copy(),
                    'props': props_to_track_group
                }
                current_state['group_objects'][child_name] = group_obj_state
                queue.append(actual_child_obj)

            elif actual_child_obj.get(constants.SDF_CANVAS_MARKER, False) and is_visible:
                props_to_track_canvas = {}
                props_to_track_canvas[constants.SDF_LINK_TARGET_NAME_PROP] = actual_child_obj.get(constants.SDF_LINK_TARGET_NAME_PROP, "")
                for key, default_val in constants.DEFAULT_CANVAS_SETTINGS.items():
                    if key != constants.SDF_LINK_TARGET_NAME_PROP:
                        props_to_track_canvas[key] = utils.get_sdf_param(actual_child_obj, key, default_val)

                canvas_obj_state = {
                    'matrix': actual_child_obj.matrix_world.copy(),
                    'props': props_to_track_canvas
                }
                current_state['canvas_objects'][child_name] = canvas_obj_state
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

    current_canvas_names = set(current_state.get('canvas_objects', {}).keys())
    cached_canvas_names = set(cached_state.get('canvas_objects', {}).keys())
    if current_canvas_names != cached_canvas_names:
        return True
    current_canvases = current_state.get('canvas_objects', {})
    cached_canvases = cached_state.get('canvas_objects', {})
    for obj_name, current_canvas_obj_state in current_canvases.items():
        cached_canvas_obj_state = cached_canvases.get(obj_name)
        if not cached_canvas_obj_state: return True # Should be caught by key set check
        if not utils.compare_matrices(current_canvas_obj_state.get('matrix'), cached_canvas_obj_state.get('matrix')):
            return True
        if not utils.compare_dicts(current_canvas_obj_state.get('props'), cached_canvas_obj_state.get('props')):
            return True

    return False