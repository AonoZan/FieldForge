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

# Stores active bpy.app.timer references for viewport updates
_debounce_timers = {}
# Caches the state dictionary that triggered the last debounce timer start
_last_trigger_states = {}
# Flags indicating an update is scheduled or running for a specific bounds
_updates_pending = {}
# Stores the time.time() when the last update finished (for throttling)
_last_update_finish_times = {}
# Caches the last known state dictionary used for a successful update
_sdf_update_caches = {}
# State for multithreading
_worker_threads = {}


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

def check_and_trigger_update(scene: bpy.types.Scene, bounds_name: str, reason: str="unknown"):
    """
    Checks if an update is needed for a specific bounds hierarchy based on state change.
    If needed and auto-update is on, resets the debounce timer for viewport updates.
    """
    global _updates_pending, _sdf_update_caches # Access relevant state dicts

    t_start = time.perf_counter()
    context = bpy.context
    if not context or not scene: return # Context/Scene might not be ready

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
        # State has changed, schedule a new debounce timer
        schedule_new_debounce_timer(scene, bounds_name, current_state)
    t_end = time.perf_counter()


def cancel_debounce_timer(bounds_name: str):
    """Cancels the active debounce timer for a specific bounds object."""
    global _debounce_timers
    timer = _debounce_timers.pop(bounds_name, None) # Get and remove timer reference
    if timer is not None and bpy.app.timers: # Check timers module exists
        try:
            if bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
        except (ValueError, TypeError, ReferenceError): pass # Timer already gone/invalid
        except Exception as e: print(f"FieldForge WARN: Unexpected error cancelling timer for {bounds_name}: {e}")


def schedule_new_debounce_timer(scene: bpy.types.Scene, bounds_name: str, trigger_state: dict):
    """ Schedules a new viewport update timer, cancelling any existing one for this bounds. """
    global _debounce_timers, _last_trigger_states
    context = bpy.context
    if not context or not scene: return

    bounds_obj = scene.objects.get(bounds_name)
    if not bounds_obj: return # Bounds deleted

    # Cancel any previous timer for this specific bounds object
    cancel_debounce_timer(bounds_name)
    # Also cancel any running worker thread for this bounds, as a new update is coming
    _cancel_and_clear_worker(bounds_name)

    # Store the state that triggered this timer scheduling attempt
    _last_trigger_states[bounds_name] = trigger_state

    # Get the delay from the bounds object's settings
    delay = utils.get_bounds_setting(bounds_obj, "sdf_realtime_update_delay")

    try:
        safe_delay = max(0.0, delay)
        # Use a lambda that captures the specific bounds_name AND scene
        # Pass scene explicitly as context might change when timer fires
        new_timer = bpy.app.timers.register(
            lambda scn=scene, name=bounds_name: debounce_check_and_run_viewport_update(scn, name),
            first_interval=safe_delay
        )
        _debounce_timers[bounds_name] = new_timer
    except Exception as e:
         print(f"FieldForge ERROR: Failed to register debounce timer for {bounds_name}: {e}")
         _last_trigger_states.pop(bounds_name, None) # Clean up trigger state on failure


def debounce_check_and_run_viewport_update(scene: bpy.types.Scene, bounds_name: str):
    """
    Timer callback. Checks throttle and schedules the actual update via another timer.
    Returns None to indicate the timer should not repeat automatically.
    """
    global _debounce_timers, _last_trigger_states, _updates_pending, _last_update_finish_times

    # Check if scene still exists when timer fires
    if not scene or not scene.name: # Check name as extra validation
         _debounce_timers.pop(bounds_name, None) # Remove timer ref if scene is gone
         return None

    bounds_obj = scene.objects.get(bounds_name)
    if not bounds_obj:
        _debounce_timers.pop(bounds_name, None) # Remove timer ref
        return None # Bounds deleted, timer is now defunct

    # Timer has fired, remove its reference (it won't fire again unless rescheduled)
    _debounce_timers.pop(bounds_name, None)

    # Check if an update was already manually triggered or is running
    if _updates_pending.get(bounds_name, False):
        return None # Let the existing pending update run its course

    # Retrieve the state that caused this timer to be scheduled
    state_to_pass_to_update = _last_trigger_states.get(bounds_name)
    if state_to_pass_to_update is None:
        return None # Should not happen if scheduling works correctly

    # --- Throttle Check ---
    min_interval = utils.get_bounds_setting(bounds_obj, "sdf_minimum_update_interval")
    last_finish = _last_update_finish_times.get(bounds_name, 0.0)
    current_time = time.time()
    time_since_last_update = current_time - last_finish

    if time_since_last_update >= min_interval:
        # --- Throttle OK: Schedule the actual viewport update ---
        _last_trigger_states.pop(bounds_name, None) # Clear trigger state, it's being used
        _updates_pending[bounds_name] = True # Mark as pending

        run_sdf_update(scene, bounds_name, state_to_pass_to_update, is_viewport_update=True)

    else:
        # --- Throttle Active: Reschedule this check function ---
        remaining_wait = min_interval - time_since_last_update
        # Keep the state in _last_trigger_states for the next attempt
        # Do NOT set the pending flag yet
        # Cancel just in case (shouldn't be needed as timer was popped)
        cancel_debounce_timer(bounds_name)
        try:
            safe_wait = max(0.0, remaining_wait)
            new_timer = bpy.app.timers.register(
                lambda scn=scene, name=bounds_name: debounce_check_and_run_viewport_update(scn, name),
                first_interval=safe_wait
            )
            _debounce_timers[bounds_name] = new_timer # Store new timer ref
        except Exception as e:
            print(f"FieldForge ERROR: Failed reschedule throttle check for {bounds_name}: {e}")
            _last_trigger_states.pop(bounds_name, None) # Clear state if reschedule fails

    return None # Essential: Prevents timer from repeating automatically


# --- Core Update Function (Now split into Thread Starter and Result Applicator) ---

def _mesh_generation_worker(result_q, is_cancelled_flag, shape, mesh_args, bounds_name):
    """
    Worker function to be run in a separate thread.
    Performs the heavy mesh generation and puts the result in a queue.
    """
    if is_cancelled_flag.is_set():
        result_q.put(None) # Signal that we cancelled before starting
        return

    try:
        t_start_worker = time.perf_counter()
        # This is the slow, CPU-intensive part
        mesh_data = shape.get_mesh(**mesh_args)
        t_end_worker = time.perf_counter()
        if is_cancelled_flag.is_set():
            result_q.put(None) # Cancelled during generation, discard result
            return
        result_q.put(mesh_data) # Put the result in the queue for the main thread
    except Exception as e:
        print(f"FieldForge Thread ERROR: libfive mesh generation failed for {bounds_name}: {e}")
        result_q.put(Exception(f"Meshing failed: {e}")) # Put exception in queue to report it

def _apply_mesh_data_from_worker(bounds_name: str, trigger_state: dict, is_viewport_update: bool):
    """
    Timer callback for the main thread. Checks the result queue from the worker.
    If a result is available, it applies the new mesh data to the Blender object.
    """
    global _worker_threads, _updates_pending, _last_update_finish_times, _sdf_update_caches
    t_apply_start = time.perf_counter()

    if bounds_name not in _worker_threads:
        return None # Worker was cancelled or finished, timer is stale

    thread, result_q, _, is_cancelled_flag = _worker_threads[bounds_name]

    try:
        # Check the queue without blocking
        result_data = result_q.get_nowait()
    except queue.Empty:
        return 0.1 # No result yet, poll again in 0.1 seconds

    # --- Result is ready, process it ---
    _cancel_and_clear_worker(bounds_name) # Clean up the worker entry now that we have the result
    
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
            mesh_generation_error = True
            raise InterruptedError("Update was cancelled by a newer request.")
        if isinstance(result_data, Exception): # Worker had an error
            mesh_generation_error = True
            raise result_data

        # --- Apply mesh data (this code is moved from the old run_sdf_update) ---
        mesh_data = result_data
        sdf_settings_from_bounds = trigger_state.get('scene_settings')
        result_name = bounds_obj.get(constants.SDF_RESULT_OBJ_NAME_PROP)
        
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
            # Create a completely new mesh datablock to avoid issues with from_pydata on existing data
            old_mesh = result_obj.data
            new_mesh_bdata = bpy.data.meshes.new(name=old_mesh.name)
            result_obj.data = new_mesh_bdata

            # Remove the old mesh datablock if it has no other users
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)

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
                     try:
                        existing_mod[angle_input_identifier] = auto_smooth_angle_rad
                     except Exception: # Input might be read-only if node group is missing
                        pass

                 # Material
                 mat_name = sdf_settings_from_bounds.get("sdf_result_material_name", "")
                 if mat_name:
                     material = bpy.data.materials.get(mat_name)
                     if material:
                         if not new_mesh_bdata.materials or new_mesh_bdata.materials.get(material.name) is None:
                             new_mesh_bdata.materials.append(material)
                 elif len(new_mesh_bdata.materials) > 0:
                     new_mesh_bdata.materials.clear()

    except Exception as e:
        mesh_generation_error = True
        mesh_update_successful = False
        if result_obj and result_obj.data:
            try: result_obj.data.clear_geometry(); result_obj.data.update()
            except Exception: pass
    finally:
        if not mesh_generation_error and mesh_update_successful:
            update_sdf_cache(trigger_state, bounds_name)
        
        _last_update_finish_times[bounds_name] = time.time()
        _updates_pending[bounds_name] = False
        
        t_apply_end = time.perf_counter()

    return None # Unregister the timer

def run_sdf_update(scene: bpy.types.Scene, bounds_name: str, trigger_state: dict, is_viewport_update: bool = False):
    """
    STARTS the threaded SDF generation and mesh update process.
    """
    if not _lf_imported_ok:
        _updates_pending[bounds_name] = False
        return

    t_start = time.perf_counter()
    context = bpy.context
    if not context or not scene or not scene.name:
        if bounds_name in _updates_pending: _updates_pending[bounds_name] = False;
        return

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
    
    mesh_args = {
        'xyz_min': (min(c.x for c in world_corners), min(c.y for c in world_corners), min(c.z for c in world_corners)),
        'xyz_max': (max(c.x for c in world_corners), max(c.y for c in world_corners), max(c.z for c in world_corners)),
        'resolution': max(3, int(sdf_settings.get("sdf_viewport_resolution" if is_viewport_update else "sdf_final_resolution", 10)))
    }

    result_q = queue.Queue()
    is_cancelled_flag = threading.Event()
    
    worker_thread = threading.Thread(
        target=_mesh_generation_worker,
        args=(result_q, is_cancelled_flag, final_combined_shape, mesh_args, bounds_name)
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
    needs_visual_redraw = False # Flag if custom visuals need redraw (transform change)

    scene_geometry_updated = any(update.id == scene and update.is_updated_geometry for update in depsgraph.updates)
    if scene_geometry_updated:
        for bounds_obj_iter in utils.get_all_bounds_objects(context):
            bounds_to_recheck_due_to_direct_change.add(bounds_obj_iter.name)


    for update in depsgraph.updates:
        updated_obj = None
        if hasattr(update, 'id') and isinstance(update.id, bpy.types.ID):
            updated_obj = update.id
        
        if not updated_obj: continue
        if isinstance(updated_obj, bpy.types.Object):
            try:
                evaluated_obj = updated_obj.evaluated_get(depsgraph) if depsgraph else updated_obj
            except (ReferenceError, AttributeError): 
                continue
            if not evaluated_obj: continue
            root_bounds_for_updated = utils.find_parent_bounds(updated_obj)
            is_updated_obj_bounds_itself = updated_obj.get(constants.SDF_BOUNDS_MARKER, False)
            
            current_obj_for_check = None
            if root_bounds_for_updated:
                current_obj_for_check = root_bounds_for_updated
            elif is_updated_obj_bounds_itself:
                current_obj_for_check = updated_obj
            
            if current_obj_for_check:
                if update.is_updated_transform or \
                   update.is_updated_geometry:
                    bounds_to_recheck_due_to_direct_change.add(current_obj_for_check.name)
            if utils.is_sdf_source(updated_obj) and update.is_updated_transform:
                needs_visual_redraw = True
            for dependent_bounds_name in state.get_dependent_bounds_for_linked_object(updated_obj.name):
                bounds_to_recheck_due_to_link.add(dependent_bounds_name)
        elif isinstance(updated_obj, bpy.types.Object) and updated_obj.get(constants.SDF_BOUNDS_MARKER, False):
            bounds_to_recheck_due_to_direct_change.add(updated_obj.name)
    all_bounds_to_schedule_check = bounds_to_recheck_due_to_direct_change.union(bounds_to_recheck_due_to_link)

    if all_bounds_to_schedule_check:
        current_scene_ctx = getattr(context, 'scene', None)
        if current_scene_ctx:
            for bounds_name_to_check in all_bounds_to_schedule_check:
                if current_scene_ctx.objects.get(bounds_name_to_check):
                    try:
                        bpy.app.timers.register(
                            lambda scn_arg=current_scene_ctx, name_arg=bounds_name_to_check: check_and_trigger_update(scn_arg, name_arg, "depsgraph_or_link_event"),
                            first_interval=0.0
                        )
                    except Exception as e: print(f"FieldForge ERROR: Failed schedule check_trigger from depsgraph for {bounds_name_to_check}: {e}")
        else: print("FieldForge WARN (Depsgraph): Cannot trigger update - no current scene.")


    # Trigger visual redraw if needed (call function from drawing module)
    if needs_visual_redraw:
        # Assumes drawing module is imported
        try:
            import importlib # Use importlib if used for reloading
            from .. import drawing
            importlib.reload(drawing) # Reload drawing if needed for dev
            drawing.tag_redraw_all_view3d()
        except (ImportError, AttributeError, NameError):
            print("FieldForge WARN (Depsgraph): Could not trigger visual redraw (drawing module issue?).")
        except Exception as e:
            print(f"FieldForge ERROR (Depsgraph): Error triggering redraw: {e}")


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
                scene = context.scene # Get scene inside loop? Probably fine outside too.
                if scene:
                    # Stagger checks slightly
                    bpy.app.timers.register(
                         lambda scn=scene, name=bounds_obj.name: check_and_trigger_update(scn, name, "initial_check"),
                         first_interval=0.1 + count * 0.05
                    )
                    count += 1
            except Exception as e:
                print(f"FieldForge ERROR: Failed schedule initial check for {bounds_obj.name}: {e}")

    if count > 0:
        print(f"FieldForge: Scheduled initial checks for {count} bounds systems.")
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
    global _debounce_timers, _last_trigger_states, _updates_pending, _last_update_finish_times, _sdf_update_caches, _worker_threads
    if bpy.app.timers: # Check if timers module is still valid
        for bounds_name in list(_debounce_timers.keys()):
             cancel_debounce_timer(bounds_name) # Use existing cancel function
    
    # Cancel all running worker threads
    for bounds_name in list(_worker_threads.keys()):
        _cancel_and_clear_worker(bounds_name)

    _debounce_timers.clear()
    _last_trigger_states.clear()
    _updates_pending.clear()
    _last_update_finish_times.clear()
    _sdf_update_caches.clear()
    _worker_threads.clear()

    clear_link_caches()