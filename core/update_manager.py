"""
Manages the automatic update process for FieldForge SDF systems.
Includes debouncing, throttling, triggering mesh regeneration,
and the Blender dependency graph handler.
This version uses multi-threading for mesh generation to keep the UI responsive.
"""

import bpy
import time
import math
from mathutils import Matrix, Vector
import threading
import queue

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
    # Define dummy lf object if needed for type hinting or basic structure,
    # but run_sdf_update will check _lf_imported_ok anyway.
    class LFDummy:
        def emptiness(self): return None # Simulate emptiness
    lf = LFDummy()


# --- Global State Dictionaries (Managed by this module) ---
# Keys are generally bounds_obj.name

# Flags indicating an update is scheduled or running for a specific bounds
_updates_pending = {}
# Caches the last known state dictionary used for a successful update
_sdf_update_caches = {}
# State for multithreading
_worker_threads = {}
# Stores the current div being displayed for each bounds object
_current_divs = {}
# Stores the target div for each bounds object (can be lower than current)
_target_divs = {}

MAX_DIV = 5 # Corresponds to lowest resolution (highest div)
MIN_DIV = 0 # Corresponds to highest resolution (lowest div)


def clear_link_caches(): # Call from clear_timers_and_state
    state.clear_link_caches()

# --- Cache Update ---

def update_sdf_cache(new_state: dict, bounds_name: str):
    """ Updates the cache for a specific bounds object with the new state. """
    global _sdf_update_caches
    if new_state and bounds_name:
        # State should already contain copies (e.g., matrix.copy())
        _sdf_update_caches[bounds_name] = new_state


# --- Debounce and Throttle Logic (Per Bounds) ---

def check_and_trigger_update(bounds_name: str, reason: str="unknown"):
    """
    Checks if an update is needed for a specific bounds hierarchy based on state change.
    If needed and auto-update is on, resets the debounce timer for viewport updates.
    """
    global _updates_pending, _sdf_update_caches # Access relevant state dicts

    is_viewport_update = True # This function is always called for auto-updates (viewport context)

    t_start = time.perf_counter()
    context = bpy.context
    if not context or not context.scene: return # Context/Scene might not be ready

    scene = context.scene
    bounds_obj = scene.objects.get(bounds_name)
    if not bounds_obj or not bounds_obj.get(constants.SDF_BOUNDS_MARKER):
        # Clean up potentially orphaned state if object is gone
        _debounce_timers.pop(bounds_name, None)
        _last_trigger_states.pop(bounds_name, None)
        _updates_pending.pop(bounds_name, None)
        _last_update_finish_times.pop(bounds_name, None)
        _sdf_update_caches.pop(bounds_name, None)
        _cancel_and_clear_worker(bounds_name)
        return

    # Check the auto-update setting ON THE BOUNDS OBJECT
    if not utils.get_bounds_setting(bounds_obj, "sdf_auto_update"):
        return # Auto update disabled for this system

    # Don't re-trigger if an update is already pending/running for this bounds
    if _updates_pending.get(bounds_name, False):
        return

    # Get the current state ONLY if necessary checks pass
    t_state_start = time.perf_counter()
    current_state = state.get_current_sdf_state(context, bounds_obj)
    t_state_end = time.perf_counter()
    if not current_state: # Handle case where state gathering fails
        print(f"FieldForge WARN (check_trigger): Could not get current state for {bounds_name}.")
        return

    # Compare current state to the cached state for this specific bounds
    cached_state = _sdf_update_caches.get(bounds_name)
    if state.has_state_changed(current_state, cached_state): # Pass cached state directly
        # Check if the ONLY change is sdf_final_resolution for viewport updates
        if is_viewport_update and cached_state is not None and \
           len(current_state) == len(cached_state) and \
           all(k in cached_state and current_state[k] == cached_state[k] for k in current_state if k != 'scene_settings') and \
           current_state.get('scene_settings', {}).get('sdf_final_resolution') != cached_state.get('scene_settings', {}).get('sdf_final_resolution'):
            # If only sdf_final_resolution changed, and it's a viewport update, do NOT trigger update
            return

        # State has changed, directly trigger update (no debounce/throttle)
        run_sdf_update(bounds_name, current_state, is_viewport_update=True)
    t_end = time.perf_counter()





# --- Core Update Function (Now split into Thread Starter and Result Applicator) ---

def _mesh_generation_worker(result_q, is_cancelled_flag, shape, bounds_name, base_resolution, current_div, xyz_min, xyz_max):
    """
    Worker function to be run in a separate thread.
    Performs the heavy mesh generation and puts the result in a queue.
    """
    if is_cancelled_flag.is_set():
        result_q.put(None) # Signal that we cancelled before starting
        return

    try:
        t_start_worker = time.perf_counter()
        # Calculate actual resolution from base_resolution and current_div
        actual_resolution = max(3, int(base_resolution / (1 << current_div)))
        mesh_args = {
            'xyz_min': xyz_min,
            'xyz_max': xyz_max,
            'resolution': actual_resolution
        }
        # This is the slow, CPU-intensive part
        mesh_data = shape.get_mesh(**mesh_args)
        t_end_worker = time.perf_counter()
        meshing_time = t_end_worker - t_start_worker
        if is_cancelled_flag.is_set():
            result_q.put(None) # Cancelled during generation, discard result
            return
        result_q.put((mesh_data, meshing_time, current_div)) # Put the result, time, and div in the queue
    except Exception as e:
        print(f"FieldForge Thread ERROR: libfive mesh generation failed for {bounds_name}: {e}")
        result_q.put(Exception(f"Meshing failed: {e}")) # Put exception in queue to report it

def _apply_mesh_data_from_worker(bounds_name: str, trigger_state: dict, is_viewport_update: bool):
    """
    Timer callback for the main thread. Checks the result queue from the worker.
    If a result is available, it applies the new mesh data to the Blender object.
    """
    global _worker_threads, _updates_pending, _sdf_update_caches, _current_divs, _target_divs
    t_apply_start = time.perf_counter()

    if bounds_name not in _worker_threads:
        return None # Worker was cancelled or finished, timer is stale

    thread, result_q, timer, is_cancelled_flag = _worker_threads[bounds_name]

    try:
        # Check the queue without blocking
        result_data = result_q.get_nowait()
    except queue.Empty:
        return 0.1 # No result yet, poll again in 0.1 seconds

    # --- Result is ready, clean up worker state immediately ---
    _worker_threads.pop(bounds_name)
    is_cancelled_flag.set()
    if timer and bpy.app.timers.is_registered(timer):
        bpy.app.timers.unregister(timer)
    
    context = bpy.context
    scene = getattr(context, 'scene', None)
    if not scene:
        _updates_pending[bounds_name] = False
        return None

    bounds_obj = scene.objects.get(bounds_name)
    if not bounds_obj:
        _updates_pending[bounds_name] = False
        return None
        
    mesh_update_successful = False
    mesh_generation_error = False
    result_obj = None

    try:
        if result_data is None: # Worker was cancelled
            raise InterruptedError("Update was cancelled by a newer request.")
        if isinstance(result_data, Exception): # Worker had an error
            raise result_data

        mesh_data, meshing_time, actual_rendered_div = result_data
        sdf_settings_from_bounds = trigger_state.get('scene_settings')
        result_name = bounds_obj.get(constants.SDF_RESULT_OBJ_NAME_PROP)

        # Update current div to the one that was just rendered
        _current_divs[bounds_name] = actual_rendered_div

        # Adaptive div logic (only for viewport updates)
        if is_viewport_update:
            target_div = _target_divs.get(bounds_name, MIN_DIV)
            if meshing_time < 0.05: # Very fast, decrease target div (higher resolution)
                _target_divs[bounds_name] = max(MIN_DIV, target_div - 1)
            elif meshing_time > 0.5: # Slow, increase target div (lower resolution)
                _target_divs[bounds_name] = min(MAX_DIV, target_div + 1)
            # else: Moderate, keep target div same

        # Find or create result object
        result_obj = utils.find_result_object(context, result_name)
        if not result_obj and result_name and sdf_settings_from_bounds.get("sdf_create_result_object"):
             new_mesh_bdata = bpy.data.meshes.new(name=result_name + "_Mesh")
             result_obj = bpy.data.objects.new(result_name, new_mesh_bdata)
             link_collection = bounds_obj.users_collection[0] if bounds_obj.users_collection else scene.collection
             link_collection.objects.link(result_obj)
             result_obj.matrix_world = Matrix.Identity(4) 
             result_obj.hide_select = True
        
        if result_obj and result_obj.type == 'MESH':
            # Update the existing mesh datablock
            new_mesh_bdata = result_obj.data
            new_mesh_bdata.clear_geometry() # Clear existing data

            if mesh_data and mesh_data[0]:
                new_mesh_bdata.from_pydata(mesh_data[0], [], mesh_data[1])
            new_mesh_bdata.update()
            mesh_update_successful = True
            
            
            # Apply smooth shading and material logic
            if mesh_update_successful and len(new_mesh_bdata.polygons) > 0:
                 for poly in new_mesh_bdata.polygons:
                     poly.use_smooth = True
                 
                 # Auto Smooth Angle via Modifier for robustness
                 angle_input_identifier = "Input_1" # Based on the default node group
                 addon_modifier_name = "FieldForge_Smooth"
                 auto_smooth_angle_deg = sdf_settings_from_bounds.get("sdf_result_auto_smooth_angle", 45.0)
                 auto_smooth_angle_rad = math.radians(auto_smooth_angle_deg)
                 
                 existing_mod = result_obj.modifiers.get(addon_modifier_name)
                 if not existing_mod:
                     # Add the 'Smooth by Angle' geometry node group modifier
                     try:
                        # This requires an active object, so we temporarily set it
                        with context.temp_override(object=result_obj, active_object=result_obj, selected_objects=[result_obj]):
                            bpy.ops.object.modifier_add_node_group(
                                asset_library_type='ESSENTIALS', asset_library_identifier="Essentials", 
                                relative_asset_identifier="geometry_nodes/smooth_by_angle.blend/NodeTree/Smooth by Angle")
                        # The new modifier will be the last one on the stack
                        existing_mod = result_obj.modifiers[-1]
                        existing_mod.name = addon_modifier_name
                     except Exception as e_mod:
                         print(f"FF WARN: Could not add 'Smooth by Angle' node group from asset library: {e_mod}")
                 
                 if existing_mod and existing_mod.node_group and angle_input_identifier in existing_mod:
                     try: existing_mod[angle_input_identifier] = auto_smooth_angle_rad
                     except Exception: pass

                 # Material
                 mat_name = sdf_settings_from_bounds.get("sdf_result_material_name", "")
                 if mat_name:
                     material = bpy.data.materials.get(mat_name)
                     if material and (not new_mesh_bdata.materials or new_mesh_bdata.materials.get(material.name) is None):
                         new_mesh_bdata.materials.append(material)
                 elif len(new_mesh_bdata.materials) > 0:
                     new_mesh_bdata.materials.clear()

    except Exception as e:
        mesh_generation_error = True
        mesh_update_successful = False
        if not isinstance(e, InterruptedError):
            print(f"FieldForge ERROR: Failed to apply mesh data for {bounds_name}: {e}")
        if result_obj and result_obj.data:
            try: result_obj.data.clear_geometry(); result_obj.data.update()
            except Exception: pass
    finally:
        if not mesh_generation_error and mesh_update_successful:
            update_sdf_cache(trigger_state, bounds_name)
        
        
        # Progressive rendering: if current div is greater than target, schedule another update
        if is_viewport_update and _current_divs.get(bounds_name, MAX_DIV) > _target_divs.get(bounds_name, MIN_DIV):
            bounds_obj = scene.objects.get(bounds_name) # Re-fetch bounds_obj
            if bounds_obj and utils.get_bounds_setting(bounds_obj, "sdf_auto_update"):
                bpy.app.timers.register(
                    lambda: run_sdf_update(bounds_name, trigger_state, is_viewport_update),
                    first_interval=0.01 # Schedule very soon
                )
            else: # If auto-update is off or bounds_obj is gone, stop progressive updates
                _updates_pending[bounds_name] = False
        else: # Progressive rendering is complete or not a viewport update
            _updates_pending[bounds_name] = False

    return None # Unregister the timer

def run_sdf_update(bounds_name: str, trigger_state: dict, is_viewport_update: bool = False):
    """
    STARTS the threaded SDF generation and mesh update process.
    """
    if not _lf_imported_ok:
        _updates_pending[bounds_name] = False
        return

    t_start = time.perf_counter()
    context = bpy.context
    if not context or not context.scene:
        if bounds_name in _updates_pending: _updates_pending[bounds_name] = False;
        return

    scene = context.scene
    bounds_obj = scene.objects.get(bounds_name)
    if not bounds_obj:
        if bounds_name in _updates_pending: _updates_pending[bounds_name] = False;
        return

    # --- Cancel any previous worker for this bounds ---
    _cancel_and_clear_worker(bounds_name)
    
    t_sdf_start = time.perf_counter()
    sdf_settings = trigger_state.get('scene_settings')
    final_combined_shape = sdf_logic.process_sdf_hierarchy(bounds_obj, sdf_settings)
    t_sdf_end = time.perf_counter()

    if final_combined_shape is None:
        final_combined_shape = lf.emptiness()

    bounds_matrix = trigger_state.get('bounds_matrix')
    local_corners = [Vector(c) for c in ((-1,-1,-1), (1,-1,-1), (-1,1,-1), (1,1,-1), (-1,-1,1), (1,-1,1), (-1,1,1), (1,1,1))]
    world_corners = [(bounds_matrix @ c.to_4d()).xyz for c in local_corners]

    xyz_min = (min(c.x for c in world_corners), min(c.y for c in world_corners), min(c.z for c in world_corners))
    xyz_max = (max(c.x for c in world_corners), max(c.y for c in world_corners), max(c.z for c in world_corners))
    
    # Get the div for this update cycle
    # If this is the first call for this bounds object, or a new update cycle
    if bounds_name not in _current_divs or bounds_name not in _target_divs or _current_divs[bounds_name] <= _target_divs[bounds_name]:
        if is_viewport_update:
            _current_divs[bounds_name] = MAX_DIV # Start at lowest resolution (highest div)
            _target_divs[bounds_name] = MIN_DIV # Target highest resolution (lowest div)
        else: # Manual update
            _current_divs[bounds_name] = MIN_DIV # Manual updates go straight to highest resolution
            _target_divs[bounds_name] = MIN_DIV # Target is the same as current
    
    # For progressive updates (only for viewport), decrement the current div towards the target
    elif is_viewport_update and _current_divs[bounds_name] > _target_divs[bounds_name]:
        _current_divs[bounds_name] -= 1 # Decrement for the next progressive step (higher resolution)

    # The div to actually use for this meshing job
    div_for_worker = _current_divs[bounds_name]

    # Calculate the actual resolution to pass to libfive based on the div
    base_resolution_setting = sdf_settings.get("sdf_viewport_resolution" if is_viewport_update else "sdf_final_resolution", 10)
    actual_resolution_for_libfive = max(3, int(base_resolution_setting / (1 << div_for_worker)))

    mesh_args = {
        'xyz_min': (min(c.x for c in world_corners), min(c.y for c in world_corners), min(c.z for c in world_corners)),
        'xyz_max': (max(c.x for c in world_corners), max(c.y for c in world_corners), max(c.z for c in world_corners)),
        'resolution': actual_resolution_for_libfive
    }

    result_q = queue.Queue()
    is_cancelled_flag = threading.Event()
    
    worker_thread = threading.Thread(
        target=_mesh_generation_worker,
        args=(result_q, is_cancelled_flag, final_combined_shape, bounds_name, base_resolution_setting, div_for_worker, xyz_min, xyz_max)
    )
    
    result_timer = bpy.app.timers.register(
        lambda: _apply_mesh_data_from_worker(bounds_name, trigger_state, is_viewport_update),
        first_interval=0.1
    )

    _worker_threads[bounds_name] = (worker_thread, result_q, result_timer, is_cancelled_flag)
    worker_thread.start()
    t_end = time.perf_counter()


def _cancel_and_clear_worker(bounds_name: str):
    """Safely cancels and cleans up a worker thread and its timer."""
    global _worker_threads
    if bounds_name in _worker_threads:
        thread, _, timer, is_cancelled_flag = _worker_threads.pop(bounds_name)
        is_cancelled_flag.set() # Signal the thread to stop
        if timer and bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)

# --- Scene Update Handler (Dependency Graph) ---
# This needs to be persistent to stay active between file loads
@bpy.app.handlers.persistent
def ff_depsgraph_handler(scene, depsgraph):
    """ Blender dependency graph handler, called after updates. """
    if not _lf_imported_ok: return # Don't run if libfive isn't working

    # Optimizations: Exit early if Blender state is unsuitable
    context = bpy.context
    if not context or not context.window_manager or not context.window_manager.windows: return
    if bpy.app.background: return # Don't run in background mode
    screen = getattr(context, 'screen', None)
    if screen and getattr(screen, 'is_scrubbing', False): return # Avoid during timeline scrubbing

    if depsgraph is None or not hasattr(depsgraph, 'updates'): return # Check depsgraph validity

    bounds_to_recheck_due_to_direct_change = set()
    bounds_to_recheck_due_to_link = set()
    needs_visual_redraw = False

    for update in depsgraph.updates:
        updated_obj = getattr(update, 'id', None)
        if not isinstance(updated_obj, bpy.types.Object):
            continue

        try:
            evaluated_obj = updated_obj.evaluated_get(depsgraph) if depsgraph else updated_obj
        except (ReferenceError, AttributeError): 
            continue
        if not evaluated_obj: continue

        root_bounds_for_updated = utils.find_parent_bounds(updated_obj)
        is_updated_obj_bounds_itself = updated_obj.get(constants.SDF_BOUNDS_MARKER, False)
        
        # Determine if the object is relevant to any SDF system
        is_sdf_relevant = root_bounds_for_updated or is_updated_obj_bounds_itself or state.get_dependent_bounds_for_linked_object(updated_obj.name)
        if not is_sdf_relevant:
            continue

        # Trigger update checks for transform, geometry, or property changes
        if update.is_updated_transform or update.is_updated_geometry or getattr(update, 'is_updated_properties', False):
            if root_bounds_for_updated:
                bounds_to_recheck_due_to_direct_change.add(root_bounds_for_updated.name)
            elif is_updated_obj_bounds_itself:
                bounds_to_recheck_due_to_direct_change.add(updated_obj.name)
            
            for dependent_bounds_name in state.get_dependent_bounds_for_linked_object(updated_obj.name):
                bounds_to_recheck_due_to_link.add(dependent_bounds_name)

        if utils.is_sdf_source(updated_obj) and update.is_updated_transform:
            needs_visual_redraw = True

    all_bounds_to_schedule_check = bounds_to_recheck_due_to_direct_change.union(bounds_to_recheck_due_to_link)

    if all_bounds_to_schedule_check:
        for bounds_name_to_check in all_bounds_to_schedule_check:
            # No need to check if bounds_obj exists here, check_and_trigger_update does it
            bpy.app.timers.register(
                lambda name_arg=bounds_name_to_check: check_and_trigger_update(name_arg, "depsgraph_or_link_event"),
                first_interval=0.0
            )

    if needs_visual_redraw:
        try:
            from .. import drawing
            drawing.tag_redraw_all_view3d()
        except Exception: pass


# --- Initial Update Check on Load ---
# Called from register() via timer
def initial_update_check_all():
    """ Schedules an initial state check for all existing Bounds objects. """
    context = bpy.context
    if not context or not context.scene: return None # Scene might not be ready
    if not _lf_imported_ok: return None # Don't run checks if libfive failed

    count = 0
    processed_bounds = set() # Avoid scheduling multiple checks if bounds are nested/duplicated somehow

    for bounds_obj in utils.get_all_bounds_objects(context):
        if bounds_obj.name not in processed_bounds:
            processed_bounds.add(bounds_obj.name)
            try:
                # Directly trigger update (no debounce)
                check_and_trigger_update(bounds_obj.name, "initial_check")
                count += 1
            except Exception as e:
                print(f"FieldForge ERROR: Failed initial check for {bounds_obj.name}: {e}")

    if count > 0:
        print(f"FieldForge: Triggered initial checks for {count} bounds systems.")
        # Also trigger an initial redraw after checks are scheduled
        try:
            from .. import drawing # Assumes drawing module exists
            bpy.app.timers.register(drawing.tag_redraw_all_view3d, first_interval=0.2 + count * 0.05)
        except Exception: pass # Ignore redraw error if drawing module fails

    return None # Timer function should return None


# --- Cleanup Function ---
# Called from unregister()
def clear_timers_and_state():
    """Cancels all active timers and clears global state dictionaries."""
    global _updates_pending, _sdf_update_caches, _worker_threads, _current_resolutions, _target_resolutions
    # Cancel all running worker threads
    for bounds_name in list(_worker_threads.keys()):
        _cancel_and_clear_worker(bounds_name)

    _updates_pending.clear()
    _sdf_update_caches.clear()
    _worker_threads.clear()
    _current_resolutions.clear()
    _target_resolutions.clear()

    clear_link_caches()