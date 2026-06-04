# FieldForge
# Copyright (C) 2026 Dejan Petrović <aonozan@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
"""
Manages the automatic update process for FieldForge SDF systems.
Includes debouncing, throttling, triggering mesh regeneration,
and the Blender dependency graph handler.
This version uses asynchronous background threading to offload heavy
mesh calculations from the Blender main thread.
"""

import bpy
import time
import math
import threading
import ctypes
import array
import os
import sys
from mathutils import Matrix, Vector

# Use relative imports assuming this file is in FieldForge/core/
from .. import constants
from .. import utils # For find_parent_bounds, get_all_bounds_objects, get_bounds_setting, find_result_object
from . import state # For get_current_sdf_state, has_state_changed
from . import sdf_logic # For process_sdf_hierarchy

# Import libfive if available (needed for run_sdf_update)
try:
    import libfive.stdlib as lf
    _lf_imported_ok = True
except ImportError:
    _lf_imported_ok = False
    class LFDummy:
        def emptiness(self): return None # Simulate emptiness
    lf = LFDummy()


# --- Global State Dictionaries (Managed by this module) ---
# Keys are generally bounds_obj.name

# State for throttled updates
_last_update_times = {}
# Active debounce timer functions
_debounce_timers = {}
# Flags indicating an update is scheduled or running for a specific bounds
_updates_pending = {}
# Caches the last known state dictionary used for a successful update
_sdf_update_caches = {}
# Keeps track of active background meshing threads
_active_meshing_threads = {}
# Stores queued states that are waiting for the active thread to complete
_queued_updates = {}
# Stores the current div being displayed for each bounds object
_current_divs = {}
# Stores the target div for each bounds object (can be lower than current)
_target_divs = {}
# Explicitly stores the current progress ratio (0.0 to 1.0)
_update_progress = {}
# Tracks whether the current active pass is viewport-driven or manual
_update_type = {}

MAX_DIV = 5 # Corresponds to lowest resolution (highest div)
MIN_DIV = 0 # Corresponds to highest resolution (lowest div)
THROTTLE_INTERVAL = 0.15 # Minimum seconds between viewport updates during active dragging

# --- Module State ---
_progressive_timers = {} # Isolated storage for progressive refinement steps
_hierarchy_tree_cache = {} # dict: {bounds_name: (signature, HierarchyNode_root)}

def get_inverted_matrix(obj, inv_cache: dict) -> Matrix:
    """Retrieves the inverted world matrix of an object, caching the result to avoid redundant inversions."""
    obj_ptr = obj.as_pointer()
    inv = inv_cache.get(obj_ptr)
    if inv is None:
        inv = obj.matrix_world.inverted()
        inv_cache[obj_ptr] = inv
    return inv

class HierarchyNode:
    """A lightweight, pure-Python tree representation of an SDF object node."""
    def __init__(self, obj, parent_node=None):
        self.obj = obj
        self.name = obj.name
        self.parent_node = parent_node
        self.is_source = utils.is_sdf_source(obj) and not utils.is_sdf_canvas(obj)
        self.is_canvas = utils.is_sdf_canvas(obj)
        self.is_group = utils.is_sdf_group(obj)
        
        # Cache resolved link targets and processing options once during compile
        self.linked_target = None
        self.processes_linked_children = False
        if utils.is_sdf_linked(obj):
            self.linked_target = utils.get_effective_sdf_object(obj)
            self.processes_linked_children = obj.get(constants.SDF_PROCESS_LINKED_CHILDREN_PROP, False)
        
        self.children = []
        self.linked_children = [] # Resolved structural links

def build_hierarchy_tree(obj, parent_node=None) -> HierarchyNode:
    """Recursively compiles a stable pure-Python HierarchyNode tree."""
    node = HierarchyNode(obj, parent_node)
    for child in obj.children:
        if child:
            child_node = build_hierarchy_tree(child, node)
            node.children.append(child_node)
    
    # Pre-resolve linked child nodes
    if node.linked_target and node.processes_linked_children:
        for l_child in node.linked_target.children:
            if l_child:
                l_child_node = build_hierarchy_tree(l_child, node)
                node.linked_children.append(l_child_node)
    return node

def get_hierarchy_signature(bounds_obj) -> tuple:
    """Generates a fast, lightweight structural signature of the bounds hierarchy."""
    sig = []
    queue = [bounds_obj]
    visited = {bounds_obj.name}
    while queue:
        curr = queue.pop(0)
        for child in curr.children:
            if child and child.name not in visited:
                visited.add(child.name)
                sig.append((child.name, curr.name, child.get("sdf_type", "")))
                queue.append(child)
                
        # Include processes_linked_children state in structural signature
        obj_processes_linked_children = curr.get(constants.SDF_PROCESS_LINKED_CHILDREN_PROP, False)
        if utils.is_sdf_linked(curr) and obj_processes_linked_children:
            linked_target = utils.get_effective_sdf_object(curr)
            if linked_target and linked_target != curr:
                for l_child in linked_target.children:
                    if l_child and l_child.name not in visited:
                        visited.add(l_child.name)
                        sig.append((l_child.name, curr.name, l_child.get("sdf_type", "")))
                        queue.append(l_child)
    return tuple(sorted(sig))

# --- Isolated Progressive Timers ---
def _register_progressive_timer(bounds_name: str, delay: float, callback):
    """Registers a progressive refinement timer, completely separate from drag debouncing."""
    global _progressive_timers
    _cancel_progressive_timer(bounds_name)

    def timer_wrapper():
        _progressive_timers.pop(bounds_name, None)
        callback()
        return None # Do not repeat

    _progressive_timers[bounds_name] = timer_wrapper
    bpy.app.timers.register(timer_wrapper, first_interval=delay)

def _cancel_progressive_timer(bounds_name: str):
    """Cancels any pending progressive refinement timer for this bounds."""
    global _progressive_timers
    timer_func = _progressive_timers.pop(bounds_name, None)
    if timer_func:
        try:
            if bpy.app.timers.is_registered(timer_func):
                bpy.app.timers.unregister(timer_func)
        except Exception:
            pass

def clear_link_caches(): # Call from clear_timers_and_state
    state.clear_link_caches()

# --- Cache Update ---

def update_sdf_cache(new_state: dict, bounds_name: str):
    """ Updates the cache for a specific bounds object with the new state. """
    global _sdf_update_caches
    if new_state and bounds_name:
        _sdf_update_caches[bounds_name] = new_state


# --- Debounce and Throttle Helpers ---

def _register_debounce_timer(bounds_name: str, delay: float, callback):
    """Registers a debounced timer for a bounds object, cancelling any existing one."""
    global _debounce_timers
    _cancel_debounce_timer(bounds_name)

    def timer_wrapper():
        _debounce_timers.pop(bounds_name, None)
        callback()
        return None # Do not repeat

    _debounce_timers[bounds_name] = timer_wrapper
    bpy.app.timers.register(timer_wrapper, first_interval=delay)

def _cancel_debounce_timer(bounds_name: str):
    """Cancels any pending delayed timer for the specified bounds."""
    global _debounce_timers
    timer_func = _debounce_timers.pop(bounds_name, None)
    if timer_func:
        try:
            if bpy.app.timers.is_registered(timer_func):
                bpy.app.timers.unregister(timer_func)
        except Exception:
            pass

def _gather_from_node_tree(node, transform_matrix_inv, active_array_params, active_group, context, results, inv_cache):
    """Traverses the pre-compiled pure-Python tree, computing only active world matrices."""
    if not node.obj or not node.obj.visible_get(view_layer=context.view_layer):
        return

    if node.is_source:
        base_inv = get_inverted_matrix(node.obj, inv_cache)
        effective_inv = base_inv @ transform_matrix_inv
        results.append((node.obj, effective_inv, active_array_params.copy(), active_group))

    elif node.is_canvas:
        for child_node in node.children:
            if child_node.is_source and child_node.obj.get("sdf_type") in constants._2D_SHAPE_TYPES:
                base_inv = get_inverted_matrix(child_node.obj, inv_cache)
                effective_inv = base_inv @ transform_matrix_inv
                results.append((child_node.obj, effective_inv, active_array_params.copy(), active_group))

    # Recurse structural children of the node
    for child_node in node.children:
        if node.is_canvas and child_node.is_source and child_node.obj.get("sdf_type") in constants._2D_SHAPE_TYPES:
            continue
        
        child_array_params = active_array_params.copy()
        child_group = active_group
        if node.is_group:
            array_mode = utils.get_sdf_param(node.obj, "sdf_main_array_mode", 'NONE')
            if array_mode != 'NONE':
                child_group = node.obj
                child_group_inv = get_inverted_matrix(node.obj, inv_cache)
                child_array_params['mode'] = array_mode
                center_on_origin = utils.get_sdf_param(node.obj, "sdf_array_center_on_origin", True)
                
                if array_mode == 'LINEAR':
                    ax = utils.get_sdf_param(node.obj, "sdf_array_active_x", False)
                    ay = utils.get_sdf_param(node.obj, "sdf_array_active_y", False) and ax
                    az = utils.get_sdf_param(node.obj, "sdf_array_active_z", False) and ay
                    
                    nx = int(utils.get_sdf_param(node.obj, "sdf_array_count_x", 2)) if ax else 1
                    ny = int(utils.get_sdf_param(node.obj, "sdf_array_count_y", 2)) if ay else 1
                    nz = int(utils.get_sdf_param(node.obj, "sdf_array_count_z", 2)) if az else 1
                    
                    child_array_params['nx'] = nx
                    child_array_params['ny'] = ny
                    child_array_params['nz'] = nz
                    
                    child_local_pos = (child_group_inv @ child_node.obj.matrix_world).translation
                    
                    dx_val = (child_local_pos.x * 2.0 / (nx - 1)) if (ax and nx > 1) else 0.0
                    dy_val = (child_local_pos.y * 2.0 / (ny - 1)) if (ay and ny > 1) else 0.0
                    dz_val = (child_local_pos.z * 2.0 / (nz - 1)) if (az and nz > 1) else 0.0
                    
                    default_small_delta = 1.0
                    if ax and nx > 1 and abs(dx_val) < 1e-5:
                        dx_val = default_small_delta * (1 if child_local_pos.x >= 0 else -1) if abs(child_local_pos.x) < 1e-5 else dx_val
                    if ay and ny > 1 and abs(dy_val) < 1e-5:
                        dy_val = default_small_delta * (1 if child_local_pos.y >= 0 else -1) if abs(child_local_pos.y) < 1e-5 else dy_val
                    if az and nz > 1 and abs(dz_val) < 1e-5:
                        dz_val = default_small_delta * (1 if child_local_pos.z >= 0 else -1) if abs(child_local_pos.z) < 1e-5 else dz_val
                        
                    child_array_params['dx'] = dx_val
                    child_array_params['dy'] = dy_val
                    child_array_params['dz'] = dz_val
                    child_array_params['sh_x'] = child_local_pos.x
                    child_array_params['sh_y'] = child_local_pos.y
                    child_array_params['sh_z'] = child_local_pos.z
                    
                elif array_mode == 'RADIAL':
                    radial_count = int(utils.get_sdf_param(node.obj, "sdf_radial_count", 1))
                    child_array_params['radial_count'] = radial_count
                    
                    try:
                        center_prop = utils.get_sdf_param(node.obj, "sdf_radial_center", (0.0, 0.0))
                        rcx = float(center_prop[0])
                        rcy = float(center_prop[1])
                    except Exception:
                        rcx = 0.0
                        rcy = 0.0
                    
                    if center_on_origin:
                        child_array_params['radial_cx'] = 0.0
                        child_array_params['radial_cy'] = 0.0
                        child_local_pos = (child_group_inv @ child_node.obj.matrix_world).translation
                        child_array_params['radial_child_x'] = child_local_pos.x - rcx
                        child_array_params['radial_child_y'] = child_local_pos.y - rcy
                    else:
                        child_array_params['radial_cx'] = rcx
                        child_array_params['radial_cy'] = rcy
                        child_local_pos = (child_group_inv @ child_node.obj.matrix_world).translation
                        child_array_params['radial_child_x'] = child_local_pos.x
                        child_array_params['radial_child_y'] = child_local_pos.y
                        
        _gather_from_node_tree(child_node, transform_matrix_inv, child_array_params, child_group, context, results, inv_cache)

    # Recurse linked node children if active
    if node.linked_target and node.processes_linked_children:
        W_target = node.linked_target.matrix_world
        W_linker_inv = get_inverted_matrix(node.obj, inv_cache)
        reparent_inv = W_target @ W_linker_inv
        new_transform_inv = reparent_inv @ transform_matrix_inv
        
        for l_child_node in node.linked_children:
            _gather_from_node_tree(l_child_node, new_transform_inv, active_array_params, active_group, context, results, inv_cache)

def _gather_leaf_shapes_and_properties(obj, context) -> tuple[list, dict]:
    """Gathers leaf shapes and properties, returning the list and the unified inversion cache."""
    global _hierarchy_tree_cache
    bounds_name = obj.name
    
    current_sig = get_hierarchy_signature(obj)
    
    cached_item = _hierarchy_tree_cache.get(bounds_name)
    if cached_item and cached_item[0] == current_sig:
        root_node = cached_item[1]
    else:
        root_node = build_hierarchy_tree(obj)
        _hierarchy_tree_cache[bounds_name] = (current_sig, root_node)
    
    results = []
    inv_cache = {}
    default_params = {
        'mode': 'NONE',
        'nx': 1, 'ny': 1, 'nz': 1,
        'dx': 0.0, 'dy': 0.0, 'dz': 0.0,
        'sh_x': 0.0, 'sh_y': 0.0, 'sh_z': 0.0,
        'radial_count': 1,
        'radial_cx': 0.0, 'radial_cy': 0.0,
        'radial_child_x': 0.0, 'radial_child_y': 0.0
    }
    _gather_from_node_tree(root_node, Matrix.Identity(4), default_params, None, context, results, inv_cache)
    return results, inv_cache


def _ensure_sdf_material(result_obj: bpy.types.Object):
    """
    Ensures that a standard node material is created and configured 
    to map the vertex color attribute to the shader's base color input.
    """
    mat_name = "SDF_Material"
    color_attr_name = "SDF_Color"
    
    material = bpy.data.materials.get(mat_name)

    if not material:
        material = bpy.data.materials.new(name=mat_name)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        
        bsdf = nodes.get('Principled BSDF')
        if bsdf:
            nodes.remove(bsdf)

        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        
        attr_node = nodes.new(type='ShaderNodeAttribute')
        attr_node.attribute_name = color_attr_name
        attr_node.location = (-200, 200)
        
        links.new(attr_node.outputs['Color'], bsdf.inputs['Base Color'])
        
        output_node = nodes.get('Material Output')
        if output_node:
            links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])

    # Assign to the first material slot of the mesh
    if not result_obj.data.materials or result_obj.data.materials[0] != material:
        if not result_obj.data.materials:
            result_obj.data.materials.append(material)
        else:
            result_obj.data.materials[0] = material


def _resolve_tree_pointer(shape):
    """
    Robustly inspects a libfive.Shape object to extract the raw C++ pointer,
    handling nested wrappers (e.g. shape.tree.ptr) automatically.
    """
    if shape is None:
        return None
    
    # Check common pointer attribute names
    for attr in ('tree', 'ptr', '_tree', '_ptr'):
        val = getattr(shape, attr, None)
        if val is not None:
            # If the value is another object, recurse into it
            if hasattr(val, 'ptr') or hasattr(val, 'tree') or hasattr(val, '_tree') or hasattr(val, '_ptr'):
                return _resolve_tree_pointer(val)
            return val
    return None


# --- Debounce and Throttle Logic (Per Bounds) ---

def check_and_trigger_update(bounds_name: str, reason: str="unknown"):
    """
    Checks if an update is needed for a specific bounds hierarchy.
    Applies viewport throttling to prevent excessive main-thread work.
    """
    global _last_update_times, _current_divs, _target_divs, _updates_pending

    context = bpy.context
    if not context or not context.scene: 
        return

    scene = context.scene
    bounds_obj = scene.objects.get(bounds_name)
    if not bounds_obj or not bounds_obj.get(constants.SDF_BOUNDS_MARKER):
        # Clean up potentially orphaned state if object is gone
        _cancel_debounce_timer(bounds_name)
        _cancel_progressive_timer(bounds_name) # Cancel progressive timer
        _updates_pending.pop(bounds_name, None)
        _sdf_update_caches.pop(bounds_name, None)
        _active_meshing_threads.pop(bounds_name, None)
        _queued_updates.pop(bounds_name, None)
        _current_divs.pop(bounds_name, None)
        _target_divs.pop(bounds_name, None)
        _last_update_times.pop(bounds_name, None)
        return

    # Check the auto-update setting ON THE BOUNDS OBJECT
    if not utils.get_bounds_setting(bounds_obj, "sdf_auto_update"):
        return

    current_state = state.get_current_sdf_state(context, bounds_obj)
    if not current_state:
        return

    # Compare current state to the cached state
    cached_state = _sdf_update_caches.get(bounds_name)
    if not state.has_state_changed(current_state, cached_state):
        return

    # Abort any progressive refinement steps scheduled for the outdated state
    _cancel_progressive_timer(bounds_name)

    _current_divs[bounds_name] = MAX_DIV
    _target_divs[bounds_name] = MIN_DIV
    _update_progress[bounds_name] = 0.0
    _update_type[bounds_name] = 'VIEWPORT'

    now = time.perf_counter()
    last_time = _last_update_times.get(bounds_name, 0.0)
    elapsed = now - last_time

    if elapsed >= THROTTLE_INTERVAL:
        _last_update_times[bounds_name] = now
        _cancel_debounce_timer(bounds_name)
        run_sdf_update(bounds_name, current_state, is_viewport_update=True)
    else:
        remaining = THROTTLE_INTERVAL - elapsed
        _register_debounce_timer(
            bounds_name,
            remaining,
            lambda: _execute_debounced_update(bounds_name, current_state)
        )

def _execute_debounced_update(bounds_name: str, trigger_state: dict):
    global _last_update_times, _current_divs, _target_divs, _update_progress
    
    cached_state = _sdf_update_caches.get(bounds_name)
    
    if not state.has_state_changed(trigger_state, cached_state):
        return

    # Abort any progressive refinement steps scheduled for the outdated state
    _cancel_progressive_timer(bounds_name)

    _current_divs[bounds_name] = MAX_DIV
    _target_divs[bounds_name] = MIN_DIV
    _update_progress[bounds_name] = 0.0

    _last_update_times[bounds_name] = time.perf_counter()
    run_sdf_update(bounds_name, trigger_state, is_viewport_update=True)


# --- Core Update Function ---

def run_sdf_update(bounds_name: str, trigger_state: dict, is_viewport_update: bool = False):
    """
    Runs the threaded SDF generation and mesh update process.
    Meshing is offloaded to a background thread to prevent UI freezing.
    """
    if not _lf_imported_ok:
        return

    global _updates_pending, _sdf_update_caches, _active_meshing_threads, _queued_updates, _update_progress, _update_type

    # If a meshing thread is already active, queue trigger state and the viewport update flag
    if bounds_name in _active_meshing_threads:
        _queued_updates[bounds_name] = (trigger_state, is_viewport_update)
        return

    try:
        context = bpy.context
        if not context or not context.scene:
            return

        scene = context.scene
        bounds_obj = scene.objects.get(bounds_name)
        if not bounds_obj:
            return

        sdf_settings = trigger_state.get('scene_settings')

        # Recompile the combined system tree from scratch (extremely fast, stable, and accurate)
        final_combined_shape = sdf_logic.process_sdf_hierarchy(bounds_obj, sdf_settings)
        if final_combined_shape is None:
            final_combined_shape = lf.emptiness()

        # Gather color-related properties safely on the main thread and receive inverse cache
        import libfive.ffi as lf_ffi
        gathered_data, inv_cache = _gather_leaf_shapes_and_properties(bounds_obj, context)
        num_sdfs = len(gathered_data)

        sdf_bytes_list = []
        sdf_sizes = []
        matrices_list = []
        child_matrices_list = []
        colors_list = []
        
        # Parallel arrays for modifier parameters (Direction C)
        blend_factors_list = []
        clearance_offsets_list = []
        use_shell_list = []
        shell_offsets_list = []

        # Arrays for Group arrays (Direction C + Arrays)
        array_modes_list = []
        array_counts_x_list = []
        array_counts_y_list = []
        array_counts_z_list = []
        array_spacings_x_list = []
        array_spacings_y_list = []
        array_spacings_z_list = []
        array_shifts_x_list = []
        array_shifts_y_list = []
        array_shifts_z_list = []
        radial_counts_list = []
        radial_centers_x_list = []
        radial_centers_y_list = []
        radial_children_x_list = []
        radial_children_y_list = []

        for src, effective_inv, array_params, group_obj in gathered_data:
            effective_src = utils.get_effective_sdf_object(src)
            if not effective_src:
                effective_src = src

            unit_shape = sdf_logic.reconstruct_shape(effective_src)
            serialized = None
            
            tree_ptr = _resolve_tree_pointer(unit_shape)
            if tree_ptr is not None:
                serialized = lf_ffi.serialize_tree(tree_ptr)
            
            if serialized is None:
                empty_sh = lf.emptiness()
                empty_ptr = _resolve_tree_pointer(empty_sh)
                if empty_ptr is not None:
                    serialized = lf_ffi.serialize_tree(empty_ptr)
            
            if serialized is not None:
                sdf_bytes_list.append(serialized)
                sdf_sizes.append(len(serialized))

                # Compute group inverse and local transition matrix using the cache
                if group_obj is not None:
                    m_group_inv = get_inverted_matrix(group_obj, inv_cache)
                    m_group_to_child = effective_inv @ group_obj.matrix_world
                else:
                    m_group_inv = effective_inv
                    m_group_to_child = Matrix.Identity(4)

                # Write effective group inverse matrix in Column-Major order
                for col in range(4):
                    for row in range(4):
                        matrices_list.append(m_group_inv[row][col])

                # Write effective group-to-child transition matrix in Column-Major order
                for col in range(4):
                    for row in range(4):
                        child_matrices_list.append(m_group_to_child[row][col])

                # Extract shape color safely from the effective linked object
                raw_color = getattr(effective_src, "sdf_color", (0.8, 0.8, 0.8, 1.0))
                colors_list.extend(raw_color)

                # Collect Blend Factor safely from the effective linked object
                blend_val = utils.get_sdf_param(effective_src, "sdf_blend_factor", 0.0)
                blend_factors_list.append(blend_val)

                # Collect Clearance Offset (only if use_clearance is checked) safely from effective object
                if utils.get_sdf_param(effective_src, "sdf_use_clearance", False):
                    clearance_offsets_list.append(utils.get_sdf_param(effective_src, "sdf_clearance_offset", 0.0))
                else:
                    clearance_offsets_list.append(0.0)

                # Collect Shell Modifier Parameters safely from effective object
                if utils.get_sdf_param(effective_src, "sdf_use_shell", False):
                    use_shell_list.append(1)
                    shell_offsets_list.append(utils.get_sdf_param(effective_src, "sdf_shell_offset", 0.0))
                else:
                    use_shell_list.append(0)
                    shell_offsets_list.append(0.0)

                # Pack Group Array parameters
                mode_str = array_params.get('mode', 'NONE')
                mode_map = {'NONE': 0, 'LINEAR': 1, 'RADIAL': 2}
                array_modes_list.append(mode_map.get(mode_str, 0))
                
                array_counts_x_list.append(array_params.get('nx', 1))
                array_counts_y_list.append(array_params.get('ny', 1))
                array_counts_z_list.append(array_params.get('nz', 1))
                
                array_spacings_x_list.append(array_params.get('dx', 0.0))
                array_spacings_y_list.append(array_params.get('dy', 0.0))
                array_spacings_z_list.append(array_params.get('dz', 0.0))

                array_shifts_x_list.append(array_params.get('sh_x', 0.0))
                array_shifts_y_list.append(array_params.get('sh_y', 0.0))
                array_shifts_z_list.append(array_params.get('sh_z', 0.0))
                
                radial_counts_list.append(array_params.get('radial_count', 1))
                radial_centers_x_list.append(array_params.get('radial_cx', 0.0))
                radial_centers_y_list.append(array_params.get('radial_cy', 0.0))
                
                radial_children_x_list.append(array_params.get('radial_child_x', 0.0))
                radial_children_y_list.append(array_params.get('radial_child_y', 0.0))

        all_sdf_bytes = b"".join(sdf_bytes_list)

        # 1. Main Thread: Pack contiguous ShapeConfig configuration array
        import libfive.ffi as lf_ffi
        shapes_buffer = (lf_ffi.ShapeConfig * num_sdfs)()
        c_sdf_data_sizes = (ctypes.c_int * num_sdfs)(*sdf_sizes)

        for idx, (src, effective_inv, array_params, group_obj) in enumerate(gathered_data):
            effective_src = utils.get_effective_sdf_object(src) or src
            s = shapes_buffer[idx]

            # Write group inverse matrix (Column-Major)
            m_group_inv = get_inverted_matrix(group_obj, inv_cache) if group_obj else effective_inv
            for c in range(4):
                for r in range(4):
                    s.matrix_group[c*4 + r] = m_group_inv[r][c]

            # Write child transition matrix (Column-Major)
            m_group_to_child = effective_inv @ group_obj.matrix_world if group_obj else Matrix.Identity(4)
            for c in range(4):
                for r in range(4):
                    s.matrix_child[c*4 + r] = m_group_to_child[r][c]

            # Write color
            raw_color = getattr(effective_src, "sdf_color", (0.8, 0.8, 0.8, 1.0))
            s.color[0], s.color[1], s.color[2], s.color[3] = raw_color

            s.blend_factor = utils.get_sdf_param(effective_src, "sdf_blend_factor", 0.0)
            s.clearance_offset = utils.get_sdf_param(effective_src, "sdf_clearance_offset", 0.0) if utils.get_sdf_param(effective_src, "sdf_use_clearance", False) else 0.0
            
            s.use_shell = 1 if utils.get_sdf_param(effective_src, "sdf_use_shell", False) else 0
            s.shell_offset = utils.get_sdf_param(effective_src, "sdf_shell_offset", 0.0)

            # Write Array properties
            mode_map = {'NONE': 0, 'LINEAR': 1, 'RADIAL': 2}
            s.array_mode = mode_map.get(array_params.get('mode', 'NONE'), 0)
            
            s.array_count_x = array_params.get('nx', 1)
            s.array_count_y = array_params.get('ny', 1)
            s.array_count_z = array_params.get('nz', 1)
            
            s.array_spacing_x = array_params.get('dx', 0.0)
            s.array_spacing_y = array_params.get('dy', 0.0)
            s.array_spacing_z = array_params.get('dz', 0.0)
            
            s.array_shifts_x = array_params.get('sh_x', 0.0)
            s.array_shifts_y = array_params.get('sh_y', 0.0)
            s.array_shifts_z = array_params.get('sh_z', 0.0)
            
            s.radial_count = array_params.get('radial_count', 1)
            s.radial_cx = array_params.get('radial_cx', 0.0)
            s.radial_cy = array_params.get('radial_cy', 0.0)
            s.radial_child_x = array_params.get('radial_child_x', 0.0)
            s.radial_child_y = array_params.get('radial_child_y', 0.0)

        # Calculate bounding box bounds
        bounds_matrix = trigger_state.get('bounds_matrix')
        local_corners = [Vector(c) for c in ((-1,-1,-1), (1,-1,-1), (-1,1,-1), (1,1,-1), (-1,-1,1), (1,-1,1), (-1,1,1), (1,1,1))]
        world_corners = [(bounds_matrix @ c.to_4d()).xyz for c in local_corners]

        xyz_min = (min(c.x for c in world_corners), min(c.y for c in world_corners), min(c.z for c in world_corners))
        xyz_max = (max(c.x for c in world_corners), max(c.y for c in world_corners), max(c.z for c in world_corners))
        
        # 1. Adjust progressive division levels and progress tracking
        if is_viewport_update:
            _update_type[bounds_name] = 'VIEWPORT'
            if bounds_name not in _current_divs or bounds_name not in _target_divs:
                _current_divs[bounds_name] = MAX_DIV
                _target_divs[bounds_name] = MIN_DIV
            elif _current_divs[bounds_name] > _target_divs[bounds_name]:
                _current_divs[bounds_name] -= 1
            div_to_use = _current_divs[bounds_name]
            
            div_range = MAX_DIV - MIN_DIV
            _update_progress[bounds_name] = (MAX_DIV - div_to_use) / div_range if div_range > 0 else 1.0
        else:
            _update_type[bounds_name] = 'MANUAL'
            _current_divs[bounds_name] = MAX_DIV
            _target_divs[bounds_name] = MIN_DIV
            div_to_use = MIN_DIV
            _update_progress[bounds_name] = 0.0

        # 2. Get the base resolution setting (viewport or final)
        base_resolution_setting = sdf_settings.get("sdf_viewport_resolution" if is_viewport_update else "sdf_final_resolution", 10)

        # 3. Calculate actual resolution using the base resolution
        actual_resolution = max(3, int(base_resolution_setting / (1 << div_to_use)))

        # 4. Define the background worker logic
        def _bg_meshing_worker():
            try:
                t_mesh_start = time.perf_counter()
                mesh_data = final_combined_shape.get_mesh(
                    xyz_min=xyz_min,
                    xyz_max=xyz_max,
                    resolution=actual_resolution
                )
                meshing_time = time.perf_counter() - t_mesh_start

                # Calculate vertex color mappings asynchronously inside C++
                calculated_colors = None
                c_utils_lib = getattr(lf_ffi, 'custom_c_utils', None)
                if c_utils_lib is not None and num_sdfs > 0 and mesh_data and mesh_data[0]:
                    try:
                        num_verts = len(mesh_data[0])

                        # High-performance zero-copy ctypes float array using Python's C-implemented array module
                        flat_verts_array = array.array('f')
                        for vert in mesh_data[0]:
                            flat_verts_array.extend(vert)
                        
                        c_verts = (ctypes.c_float * (num_verts * 3)).from_buffer(flat_verts_array)
                        c_colors_out = (ctypes.c_float * (num_verts * 4))()

                        # 1. Create context (Deserializes trees in the background)
                        c_sdf_data = (ctypes.c_uint8 * len(all_sdf_bytes)).from_buffer_copy(all_sdf_bytes)
                        ctx = c_utils_lib.create_context(c_sdf_data, c_sdf_data_sizes, shapes_buffer, num_sdfs)
                        
                        # 2. Evaluate colors (OpenMP-parallelized across all CPU cores)
                        c_utils_lib.calculate_colors_context(ctx, c_verts, num_verts, c_colors_out)
                        
                        # 3. Destroy context
                        c_utils_lib.destroy_context(ctx)

                        calculated_colors = list(c_colors_out)
                    except Exception as e_col:
                        print(f"FieldForge ERROR: Color evaluation failed: {e_col}")

               # Safely update Blender's mesh on the main thread
                def main_thread_callback():
                    global _active_meshing_threads, _queued_updates, _updates_pending
                    try:
                        ctx = bpy.context
                        if not ctx or not ctx.scene:
                            return None
                        b_obj = ctx.scene.objects.get(bounds_name)
                        if b_obj:
                            _apply_mesh_data(
                                b_obj, trigger_state, mesh_data, 
                                meshing_time, div_to_use, is_viewport_update, 
                                colors_data=calculated_colors
                            )
                    except Exception as e_apply:
                        print(f"FieldForge ERROR: Failed to apply background mesh data: {e_apply}")
                    finally:
                        _active_meshing_threads.pop(bounds_name, None)
                        
                        if is_viewport_update:
                            _updates_pending[bounds_name] = False
                        else:
                            # Keep it showing Green (100%) for 0.5 seconds so the user gets a clear success cue
                            def delay_clear_pending():
                                _updates_pending[bounds_name] = False
                                _current_divs[bounds_name] = 0
                                return None
                            bpy.app.timers.register(delay_clear_pending, first_interval=0.5)

                        # Trigger the next queued update if states changed during worker thread runtime
                        queued_item = _queued_updates.pop(bounds_name, None)
                        if queued_item:
                            next_state, next_is_viewport = queued_item
                            
                            # Discard the queued update if we have already successfully finalized the exact same state
                            cached_state = _sdf_update_caches.get(bounds_name)
                            if next_is_viewport and _current_divs.get(bounds_name, MAX_DIV) == MIN_DIV and not state.has_state_changed(next_state, cached_state):
                                pass 
                            else:
                                # A true state change is present in the queued update.
                                # Reset progressive refinement so the new state compiles progressively starting at MAX_DIV.
                                if next_is_viewport:
                                    _current_divs[bounds_name] = MAX_DIV
                                    _target_divs[bounds_name] = MIN_DIV
                                    _update_progress[bounds_name] = 0.0
                                run_sdf_update(bounds_name, next_state, is_viewport_update=next_is_viewport)
                    return None

                bpy.app.timers.register(main_thread_callback)

            except Exception as e_mesh:
                print(f"FieldForge ERROR: Background meshing failed: {e_mesh}")
                def cleanup_callback():
                    global _active_meshing_threads, _updates_pending
                    _active_meshing_threads.pop(bounds_name, None)
                    _updates_pending[bounds_name] = False
                    return None
                bpy.app.timers.register(cleanup_callback)

        # Spawn background meshing thread
        thread = threading.Thread(target=_bg_meshing_worker, daemon=True)
        _active_meshing_threads[bounds_name] = thread
        _updates_pending[bounds_name] = True
        thread.start()

    except Exception as e:
        print(f"FieldForge ERROR: Failed to launch background meshing thread: {e}")
        _updates_pending[bounds_name] = False


def _apply_mesh_data(bounds_obj, trigger_state: dict, mesh_data, meshing_time: float, actual_rendered_div: int, is_viewport_update: bool, colors_data=None):
    global _current_divs, _target_divs, _update_progress, _update_type
    bounds_name = bounds_obj.name
    context = bpy.context
    scene = context.scene

    # Adaptive progressive level updates
    if is_viewport_update:
        _current_divs[bounds_name] = actual_rendered_div
        div_range = MAX_DIV - MIN_DIV
        _update_progress[bounds_name] = (MAX_DIV - actual_rendered_div) / div_range if div_range > 0 else 1.0

        target_div = _target_divs.get(bounds_name, MIN_DIV)
        if meshing_time < 0.05:
            _target_divs[bounds_name] = max(MIN_DIV, target_div - 1)
        elif meshing_time > 0.5:
            _target_divs[bounds_name] = min(MAX_DIV, target_div + 1)
    else:
        # Manual update completed successfully, set progress to 100% (Green)
        _current_divs[bounds_name] = MIN_DIV
        _update_progress[bounds_name] = 1.0

    sdf_settings_from_bounds = trigger_state.get('scene_settings')
    result_name = bounds_obj.get(constants.SDF_RESULT_OBJ_NAME_PROP)

    result_obj = utils.find_result_object(context, result_name)
    if not result_obj and result_name and sdf_settings_from_bounds.get("sdf_create_result_object"):
         new_mesh_bdata = bpy.data.meshes.new(name=result_name + "_Mesh")
         result_obj = bpy.data.objects.new(result_name, new_mesh_bdata)
         link_collection = bounds_obj.users_collection[0] if bounds_obj.users_collection else scene.collection
         link_collection.objects.link(result_obj)
         result_obj.matrix_world = Matrix.Identity(4) 
         result_obj.hide_select = True
    
    mesh_update_successful = False
    if result_obj and result_obj.type == 'MESH':
        new_mesh_bdata = result_obj.data
        new_mesh_bdata.clear_geometry()

        if mesh_data and mesh_data[0] and mesh_data[1]:
            num_verts = len(mesh_data[0])
            num_tris = len(mesh_data[1])
            try:
                # Pre-allocate exact structures directly to bypass safe/slow validations
                new_mesh_bdata.vertices.add(num_verts)
                new_mesh_bdata.loops.add(num_tris * 3)
                new_mesh_bdata.polygons.add(num_tris)

                # Direct write of vertices using fast C-arrays
                flat_verts = array.array('f')
                for vert in mesh_data[0]:
                    flat_verts.extend(vert)
                new_mesh_bdata.vertices.foreach_set("co", flat_verts)

                # Direct write of loop indices using fast C-arrays
                flat_loops = array.array('i')
                for tri in mesh_data[1]:
                    flat_loops.extend(tri)
                new_mesh_bdata.loops.foreach_set("vertex_index", flat_loops)

                # Direct write of polygons (triangles) using pre-allocated C-buffers
                loop_starts = array.array('i', range(0, num_tris * 3, 3))
                loop_totals = array.array('i', [3]) * num_tris
                
                new_mesh_bdata.polygons.foreach_set("loop_start", loop_starts)
                new_mesh_bdata.polygons.foreach_set("loop_total", loop_totals)

                # Map calculated vertex colors directly to Mesh Point Attributes
                if colors_data and len(colors_data) == num_verts * 4:
                    color_layer_name = "SDF_Color"
                    color_attr = new_mesh_bdata.color_attributes.get(color_layer_name)
                    if not color_attr:
                        color_attr = new_mesh_bdata.color_attributes.new(
                            name=color_layer_name,
                            type='FLOAT_COLOR',
                            domain='POINT'
                        )
                    # Write color data using a pre-allocated float buffer
                    c_colors_data = array.array('f', colors_data)
                    color_attr.data.foreach_set("color", c_colors_data)

                mesh_update_successful = True
            except Exception as e_fast:
                print(f"FieldForge WARN: Fast mesh copy failed, falling back: {e_fast}")
                # Safe fallback to standard from_pydata
                new_mesh_bdata.clear_geometry()
                new_mesh_bdata.from_pydata(mesh_data[0], [], mesh_data[1])
                mesh_update_successful = True
        
        new_mesh_bdata.update()
        
        # Apply smooth shading and modifier logic
        if mesh_update_successful and len(new_mesh_bdata.polygons) > 0:
             addon_modifier_name = "FieldForge_Smooth"
             auto_smooth_angle_deg = sdf_settings_from_bounds.get("sdf_result_auto_smooth_angle", 45.0)
             auto_smooth_angle_rad = math.radians(auto_smooth_angle_deg)
             
             existing_mod = result_obj.modifiers.get(addon_modifier_name)
             if not existing_mod:
                 try:
                     existing_mod = result_obj.modifiers.new(name=addon_modifier_name, type='NODES')
                     existing_mod.node_group = utils.get_or_create_smooth_node_group()
                 except Exception as e_mod:
                     print(f"FF WARN: Could not dynamically assign 'Smooth by Angle' modifier: {e_mod}")
             
             # Locate socket identifier dynamically by name to support future Blender modifications
             angle_input_identifier = "Socket_2" # Set "Socket_2" as the default fallback
             if existing_mod and existing_mod.node_group and hasattr(existing_mod.node_group, 'interface'):
                 try:
                     interface = existing_mod.node_group.interface
                     items_prop = bpy.types.NodeTreeInterface.items.__get__(interface, bpy.types.NodeTreeInterface)
                     for item in items_prop:
                         item_type = getattr(item, 'item_type', '')
                         in_out = getattr(item, 'in_out', '')
                         if item_type == 'SOCKET' and in_out == 'INPUT' and item.name == 'Angle':
                             angle_input_identifier = item.identifier
                             break
                 except Exception:
                     # Fallback to deterministic default "Socket_2" if custom lookup fails
                     pass
             
             if existing_mod and angle_input_identifier in existing_mod:
                 try: 
                     existing_mod[angle_input_identifier] = auto_smooth_angle_rad
                 except Exception: 
                     pass

             # Viewport optimization: disable modifier evaluation during viewport updates to eliminate modifier stutters
             # It turns back on for the high-resolution step (div == MIN_DIV)
             if existing_mod:
                 existing_mod.show_viewport = (not is_viewport_update) or (actual_rendered_div == MIN_DIV)

             # Set Material properties
             mat_name = sdf_settings_from_bounds.get("sdf_result_material_name", "")
             if mat_name:
                 material = bpy.data.materials.get(mat_name)
                 if material:
                     if not new_mesh_bdata.materials:
                         new_mesh_bdata.materials.append(material)
                     else:
                         new_mesh_bdata.materials[0] = material
             else:
                 # Auto-provision standard color attribute shader if computed colors are present
                 if colors_data:
                     _ensure_sdf_material(result_obj)
                 elif len(new_mesh_bdata.materials) > 0:
                     new_mesh_bdata.materials.clear()

    update_sdf_cache(trigger_state, bounds_name)

    # Schedule progressive refinement to higher resolution steps
    if is_viewport_update and _current_divs.get(bounds_name, MAX_DIV) > _target_divs.get(bounds_name, MIN_DIV):
        # Skip progressive refinement of this state if a newer state is already queued
        if bounds_name not in _queued_updates:
            if utils.get_bounds_setting(bounds_obj, "sdf_auto_update"):
                _register_progressive_timer(
                    bounds_name,
                    0.01,
                    lambda: run_sdf_update(bounds_name, trigger_state, is_viewport_update)
                )


# --- Scene Update Handler (Dependency Graph) ---

@bpy.app.handlers.persistent
def ff_depsgraph_handler(scene, depsgraph):
    """ Blender dependency graph handler, called after updates. """
    if not _lf_imported_ok: 
        return

    context = bpy.context
    if not context or not context.window_manager or not context.window_manager.windows: 
        return
    if bpy.app.background: 
        return
    screen = getattr(context, 'screen', None)
    if screen and getattr(screen, 'is_scrubbing', False): 
        return

    if depsgraph is None or not hasattr(depsgraph, 'updates'): 
        return

    bounds_to_recheck = set()
    needs_visual_redraw = False

    for update in depsgraph.updates:
        updated_obj = getattr(update, 'id', None)
        if not isinstance(updated_obj, bpy.types.Object):
            continue

        try:
            evaluated_obj = updated_obj.evaluated_get(depsgraph) if depsgraph else updated_obj
        except (ReferenceError, AttributeError): 
            continue
        if not evaluated_obj: 
            continue

        root_bounds = utils.find_parent_bounds(updated_obj)
        is_bounds = updated_obj.get(constants.SDF_BOUNDS_MARKER, False)
        
        is_sdf_relevant = root_bounds or is_bounds or state.get_dependent_bounds_for_linked_object(updated_obj.name)
        if not is_sdf_relevant:
            continue

        if update.is_updated_transform or update.is_updated_geometry or getattr(update, 'is_updated_properties', False):
            if root_bounds:
                bounds_to_recheck.add(root_bounds.name)
            elif is_bounds:
                bounds_to_recheck.add(updated_obj.name)
            
            for dependent_bounds_name in state.get_dependent_bounds_for_linked_object(updated_obj.name):
                bounds_to_recheck.add(dependent_bounds_name)

        if utils.is_sdf_source(updated_obj) and update.is_updated_transform:
            needs_visual_redraw = True

    if bounds_to_recheck:
        for name in bounds_to_recheck:
            check_and_trigger_update(name, "depsgraph_or_link_event")

    if needs_visual_redraw:
        try:
            from .. import drawing
            drawing.tag_redraw_all_view3d()
        except Exception: 
            pass


# --- Initial Update Check on Load ---

def initial_update_check_all():
    """ Schedules an initial state check for all existing Bounds objects. """
    context = bpy.context
    if not context or not context.scene: 
        return None
    if not _lf_imported_ok: 
        return None

    count = 0
    processed_bounds = set()

    for bounds_obj in utils.get_all_bounds_objects(context):
        if bounds_obj.name not in processed_bounds:
            processed_bounds.add(bounds_obj.name)
            try:
                check_and_trigger_update(bounds_obj.name, "initial_check")
                count += 1
            except Exception as e:
                print(f"FieldForge ERROR: Failed initial check for {bounds_obj.name}: {e}")

    if count > 0:
        print(f"FieldForge: Triggered initial checks for {count} bounds systems.")
        try:
            from .. import drawing
            bpy.app.timers.register(drawing.tag_redraw_all_view3d, first_interval=0.01)
        except Exception: 
            pass

    return None


# --- Cleanup Function ---

def clear_timers_and_state():
    """Cancels all active timers and clears global state dictionaries."""
    global _updates_pending, _sdf_update_caches, _serialized_tree_caches, _current_divs, _target_divs, _last_update_times, _debounce_timers, _active_meshing_threads, _queued_updates, _update_progress, _update_type, _progressive_timers, _hierarchy_tree_cache
    
    # Cancel all active progressive and debounce timers safely
    for bounds_name in list(_debounce_timers.keys()):
        _cancel_debounce_timer(bounds_name)
    for bounds_name in list(_progressive_timers.keys()):
        _cancel_progressive_timer(bounds_name)

    _updates_pending.clear()
    _sdf_update_caches.clear()
    _current_divs.clear()
    _target_divs.clear()
    _last_update_times.clear()
    _debounce_timers.clear()
    _progressive_timers.clear()
    _active_meshing_threads.clear()
    _queued_updates.clear()
    _update_progress.clear()
    _update_type.clear()
    _hierarchy_tree_cache.clear()

    clear_link_caches()