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
# Caches the serialized bytes representation of the compiled trees
_serialized_tree_caches = {}
# Keeps track of active background meshing threads
_active_meshing_threads = {}
# Stores queued states that are waiting for the active thread to complete
_queued_updates = {}
# Stores the current div being displayed for each bounds object
_current_divs = {}
# Stores the target div for each bounds object (can be lower than current)
_target_divs = {}

MAX_DIV = 5 # Corresponds to lowest resolution (highest div)
MIN_DIV = 0 # Corresponds to highest resolution (lowest div)
THROTTLE_INTERVAL = 0.15 # Minimum seconds between viewport updates during active dragging


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
        _updates_pending.pop(bounds_name, None)
        _sdf_update_caches.pop(bounds_name, None)
        _serialized_tree_caches.pop(bounds_name, None)
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
    if state.has_state_changed(current_state, cached_state):
        # Check if the ONLY change is sdf_final_resolution for viewport updates
        if cached_state is not None and \
           len(current_state) == len(cached_state) and \
           all(k in cached_state and current_state[k] == cached_state[k] for k in current_state if k != 'scene_settings') and \
           current_state.get('scene_settings', {}).get('sdf_final_resolution') != cached_state.get('scene_settings', {}).get('sdf_final_resolution'):
            return

        # Prepare progressive rendering step values
        _current_divs[bounds_name] = MAX_DIV
        _target_divs[bounds_name] = MIN_DIV

        now = time.perf_counter()
        last_time = _last_update_times.get(bounds_name, 0.0)
        elapsed = now - last_time

        if elapsed >= THROTTLE_INTERVAL:
            # Perform synchronous update immediately (throttled)
            _last_update_times[bounds_name] = now
            _cancel_debounce_timer(bounds_name)
            run_sdf_update(bounds_name, current_state, is_viewport_update=True)
        else:
            # Postpone/debounce update until active dragging pauses
            remaining = THROTTLE_INTERVAL - elapsed
            _register_debounce_timer(
                bounds_name,
                remaining,
                lambda: _execute_debounced_update(bounds_name, current_state)
            )

def _execute_debounced_update(bounds_name: str, trigger_state: dict):
    global _last_update_times
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

    global _updates_pending, _serialized_tree_caches, _sdf_update_caches, _active_meshing_threads, _queued_updates

    # If a meshing thread is already active, queue this update state and return
    if bounds_name in _active_meshing_threads:
        _queued_updates[bounds_name] = trigger_state
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
        
        # Check if we can reuse the serialized tree from cache to avoid expensive rebuild
        cached_state = _sdf_update_caches.get(bounds_name)
        state_changed = state.has_state_changed(trigger_state, cached_state)
        
        serialized_bytes = _serialized_tree_caches.get(bounds_name)
        final_combined_shape = None
        
        if not state_changed and serialized_bytes:
            try:
                import libfive.ffi as lf_ffi
                from libfive.shape import Shape
                tree_ptr = lf_ffi.deserialize_tree(serialized_bytes)
                if tree_ptr:
                    final_combined_shape = Shape(tree_ptr)
            except Exception as e_deser:
                print(f"FieldForge WARN: Failed to deserialize cached tree for {bounds_name}: {e_deser}")
                final_combined_shape = None

        if final_combined_shape is None:
            # Generate the libfive shape hierarchy from scratch
            final_combined_shape = sdf_logic.process_sdf_hierarchy(bounds_obj, sdf_settings)
            
            # Serialize and cache the newly compiled tree
            if final_combined_shape is not None and final_combined_shape is not lf.emptiness():
                try:
                    import libfive.ffi as lf_ffi
                    tree_attr = getattr(final_combined_shape, 'tree', None)
                    if tree_attr:
                        new_serialized = lf_ffi.serialize_tree(tree_attr)
                        if new_serialized:
                            _serialized_tree_caches[bounds_name] = new_serialized
                except Exception as e_ser:
                    print(f"FieldForge WARN: Failed to serialize tree for {bounds_name}: {e_ser}")

        if final_combined_shape is None:
            final_combined_shape = lf.emptiness()

        # Calculate bounding box bounds
        bounds_matrix = trigger_state.get('bounds_matrix')
        local_corners = [Vector(c) for c in ((-1,-1,-1), (1,-1,-1), (-1,1,-1), (1,1,-1), (-1,-1,1), (1,-1,1), (-1,1,1), (1,1,1))]
        world_corners = [(bounds_matrix @ c.to_4d()).xyz for c in local_corners]

        xyz_min = (min(c.x for c in world_corners), min(c.y for c in world_corners), min(c.z for c in world_corners))
        xyz_max = (max(c.x for c in world_corners), max(c.y for c in world_corners), max(c.z for c in world_corners))
        
        # Adjust progressive division levels
        if bounds_name not in _current_divs or bounds_name not in _target_divs:
            if is_viewport_update:
                _current_divs[bounds_name] = MAX_DIV
                _target_divs[bounds_name] = MIN_DIV
            else:
                _current_divs[bounds_name] = MIN_DIV
                _target_divs[bounds_name] = MIN_DIV
        elif is_viewport_update and _current_divs[bounds_name] > _target_divs[bounds_name]:
            _current_divs[bounds_name] -= 1

        div_to_use = _current_divs[bounds_name]
        base_resolution_setting = sdf_settings.get("sdf_viewport_resolution" if is_viewport_update else "sdf_final_resolution", 10)
        actual_resolution = max(3, int(base_resolution_setting / (1 << div_to_use)))

        # Define the background worker logic
        def _bg_meshing_worker():
            try:
                t_mesh_start = time.perf_counter()
                mesh_data = final_combined_shape.get_mesh(
                    xyz_min=xyz_min,
                    xyz_max=xyz_max,
                    resolution=actual_resolution
                )
                meshing_time = time.perf_counter() - t_mesh_start

                # Safely update Blender's mesh on the main thread
                def main_thread_callback():
                    global _active_meshing_threads, _queued_updates, _updates_pending
                    try:
                        ctx = bpy.context
                        if not ctx or not ctx.scene:
                            return None
                        b_obj = ctx.scene.objects.get(bounds_name)
                        if b_obj:
                            _apply_mesh_data(b_obj, trigger_state, mesh_data, meshing_time, div_to_use, is_viewport_update)
                    except Exception as e_apply:
                        print(f"FieldForge ERROR: Failed to apply background mesh data: {e_apply}")
                    finally:
                        _active_meshing_threads.pop(bounds_name, None)
                        _updates_pending[bounds_name] = False

                        # Trigger the next queued update if states changed during worker thread runtime
                        next_state = _queued_updates.pop(bounds_name, None)
                        if next_state:
                            run_sdf_update(bounds_name, next_state, is_viewport_update=is_viewport_update)
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


def _apply_mesh_data(bounds_obj, trigger_state: dict, mesh_data, meshing_time: float, actual_rendered_div: int, is_viewport_update: bool):
    global _current_divs, _target_divs
    bounds_name = bounds_obj.name
    context = bpy.context
    scene = context.scene

    _current_divs[bounds_name] = actual_rendered_div

    # Adaptive progressive level updates
    if is_viewport_update:
        target_div = _target_divs.get(bounds_name, MIN_DIV)
        if meshing_time < 0.05: # Fast evaluation, allow higher quality
            _target_divs[bounds_name] = max(MIN_DIV, target_div - 1)
        elif meshing_time > 0.5: # Slow evaluation, keep resolution lower
            _target_divs[bounds_name] = min(MAX_DIV, target_div + 1)

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

                # Direct write of vertices
                flat_verts = [val for vert in mesh_data[0] for val in vert]
                new_mesh_bdata.vertices.foreach_set("co", flat_verts)

                # Direct write of loop indices
                flat_loops = [idx for tri in mesh_data[1] for idx in tri]
                new_mesh_bdata.loops.foreach_set("vertex_index", flat_loops)

                # Direct write of polygons (each is a triangle)
                new_mesh_bdata.polygons.foreach_set("loop_start", range(0, num_tris * 3, 3))
                new_mesh_bdata.polygons.foreach_set("loop_total", (3,) * num_tris)

                # Enable smooth shading via array memory copy (replaces slow Python loop)
                new_mesh_bdata.polygons.foreach_set("use_smooth", (True,) * num_tris)

                mesh_update_successful = True
            except Exception as e_fast:
                print(f"FieldForge WARN: Fast mesh copy failed, falling back: {e_fast}")
                # Safe fallback to standard from_pydata
                new_mesh_bdata.clear_geometry()
                new_mesh_bdata.from_pydata(mesh_data[0], [], mesh_data[1])
                for poly in new_mesh_bdata.polygons:
                    poly.use_smooth = True
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
                 if material and (not new_mesh_bdata.materials or new_mesh_bdata.materials.get(material.name) is None):
                     new_mesh_bdata.materials.append(material)
             elif len(new_mesh_bdata.materials) > 0:
                 new_mesh_bdata.materials.clear()

    update_sdf_cache(trigger_state, bounds_name)

    # Schedule progressive refinement to higher resolution steps
    if is_viewport_update and _current_divs.get(bounds_name, MAX_DIV) > _target_divs.get(bounds_name, MIN_DIV):
        if utils.get_bounds_setting(bounds_obj, "sdf_auto_update"):
            _register_debounce_timer(
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
            bpy.app.timers.register(drawing.tag_redraw_all_view3d, first_interval=0.2 + count * 0.05)
        except Exception: 
            pass

    return None


# --- Cleanup Function ---

def clear_timers_and_state():
    """Cancels all active timers and clears global state dictionaries."""
    global _updates_pending, _sdf_update_caches, _serialized_tree_caches, _current_divs, _target_divs, _last_update_times, _debounce_timers, _active_meshing_threads, _queued_updates
    
    # Cancel all active debounce timers safely
    for bounds_name in list(_debounce_timers.keys()):
        _cancel_debounce_timer(bounds_name)

    _updates_pending.clear()
    _sdf_update_caches.clear()
    _serialized_tree_caches.clear()
    _current_divs.clear()
    _target_divs.clear()
    _last_update_times.clear()
    _debounce_timers.clear()
    _active_meshing_threads.clear()
    _queued_updates.clear()

    clear_link_caches()