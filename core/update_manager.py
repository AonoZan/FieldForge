# FieldForge/core/update_manager.py

"""
Manages the automatic update process for FieldForge SDF systems.
Includes debouncing, throttling, triggering mesh regeneration,
and the Blender dependency graph handler.
"""

import bpy
import time
import traceback # For error logging
from mathutils import Matrix

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


# --- Cache Update ---

def update_sdf_cache(new_state: dict, bounds_name: str):
    """ Updates the cache for a specific bounds object with the new state. """
    global _sdf_update_caches
    if new_state and bounds_name:
        # State should already contain copies (e.g., matrix.copy())
        _sdf_update_caches[bounds_name] = new_state
        # # print("f:DEBUG (update_cache): Updated cache for {bounds_name}") # DEBUG


# --- Debounce and Throttle Logic (Per Bounds) ---

def check_and_trigger_update(scene: bpy.types.Scene, bounds_name: str, reason: str="unknown"):
    """
    Checks if an update is needed for a specific bounds hierarchy based on state change.
    If needed and auto-update is on, resets the debounce timer for viewport updates.
    """
    global _updates_pending, _sdf_update_caches # Access relevant state dicts

    context = bpy.context
    if not context or not scene: return # Context/Scene might not be ready

    bounds_obj = scene.objects.get(bounds_name)
    if not bounds_obj or not bounds_obj.get(constants.SDF_BOUNDS_MARKER):
        # # print("f:DEBUG (check_trigger): Bounds object '{bounds_name}' not found or invalid.") # DEBUG
        # Clean up potentially orphaned state if object is gone
        _debounce_timers.pop(bounds_name, None)
        _last_trigger_states.pop(bounds_name, None)
        _updates_pending.pop(bounds_name, None)
        _last_update_finish_times.pop(bounds_name, None)
        _sdf_update_caches.pop(bounds_name, None)
        return

    # Check the auto-update setting ON THE BOUNDS OBJECT
    if not utils.get_bounds_setting(bounds_obj, "sdf_auto_update"):
        # # print("f:DEBUG (check_trigger): Auto-update disabled for {bounds_name}.") # DEBUG
        return # Auto update disabled for this system

    # Don't re-trigger if an update is already pending/running for this bounds
    if _updates_pending.get(bounds_name, False):
        # # print("f:DEBUG (check_trigger): Update already pending for {bounds_name}.") # DEBUG
        return

    # Get the current state ONLY if necessary checks pass
    current_state = state.get_current_sdf_state(context, bounds_obj)
    if not current_state: # Handle case where state gathering fails
        print(f"FieldForge WARN (check_trigger): Could not get current state for {bounds_name}.")
        return

    # Compare current state to the cached state for this specific bounds
    cached_state = _sdf_update_caches.get(bounds_name)
    # # print("f:DEBUG (check_trigger): Checking state change for {bounds_name} due to '{reason}'. Cache exists: {cached_state is not None}") # DEBUG
    if state.has_state_changed(current_state, cached_state): # Pass cached state directly
        # # print("f:DEBUG (check_trigger): State HAS changed for {bounds_name}. Scheduling debounce.") # DEBUG
        # State has changed, schedule a new debounce timer
        schedule_new_debounce_timer(scene, bounds_name, current_state)
        # Trigger redraw for custom visuals (handled in drawing.py)
        # Consider importing and calling drawing.tag_redraw_all_view3d() here if needed
        # import drawing; drawing.tag_redraw_all_view3d() # Requires careful import handling
    # else: print(f"DEBUG (check_trigger): State UNCHANGED for {bounds_name}.") # DEBUG


def cancel_debounce_timer(bounds_name: str):
    """Cancels the active debounce timer for a specific bounds object."""
    global _debounce_timers
    timer = _debounce_timers.pop(bounds_name, None) # Get and remove timer reference
    if timer is not None and bpy.app.timers: # Check timers module exists
        try:
            if bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
                # # # print("f:DEBUG (cancel_timer): Canceled timer for {bounds_name}") # DEBUG
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
        # # # print("f:DEBUG (schedule_timer): Scheduled timer for {bounds_name} with delay {safe_delay:.2f}s") # DEBUG
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
        # # # print("f:DEBUG (debounce_check): Bounds '{bounds_name}' deleted before timer fired.") # DEBUG
        _debounce_timers.pop(bounds_name, None) # Remove timer ref
        return None # Bounds deleted, timer is now defunct

    # Timer has fired, remove its reference (it won't fire again unless rescheduled)
    _debounce_timers.pop(bounds_name, None)
    # # # print("f:DEBUG (debounce_check): Debounce timer fired for {bounds_name}.") # DEBUG

    # Check if an update was already manually triggered or is running
    if _updates_pending.get(bounds_name, False):
        # # # print("f:DEBUG (debounce_check): Update already pending for {bounds_name}, skipping.") # DEBUG
        return None # Let the existing pending update run its course

    # Retrieve the state that caused this timer to be scheduled
    state_to_pass_to_update = _last_trigger_states.get(bounds_name)
    if state_to_pass_to_update is None:
        # # # print("f:DEBUG (debounce_check): No trigger state found for {bounds_name}, skipping.") # DEBUG
        return None # Should not happen if scheduling works correctly

    # --- Throttle Check ---
    min_interval = utils.get_bounds_setting(bounds_obj, "sdf_minimum_update_interval")
    last_finish = _last_update_finish_times.get(bounds_name, 0.0)
    current_time = time.time()
    time_since_last_update = current_time - last_finish

    if time_since_last_update >= min_interval:
        # --- Throttle OK: Schedule the actual viewport update ---
        # # # print("f:DEBUG (debounce_check): Throttle OK for {bounds_name}. Scheduling run_sdf_update.") # DEBUG
        _last_trigger_states.pop(bounds_name, None) # Clear trigger state, it's being used
        _updates_pending[bounds_name] = True # Mark as pending

        try:
            # Use timer with 0 interval to run the update in the next Blender tick
            # Pass the specific bounds_name, state, and scene explicitly
            bpy.app.timers.register(
                lambda scn=scene, name=bounds_name, state=state_to_pass_to_update: run_sdf_update(scn, name, state, is_viewport_update=True),
                first_interval=0.0
            )
        except Exception as e:
             print(f"FieldForge ERROR: Failed register run_sdf_update timer for {bounds_name}: {e}")
             _updates_pending[bounds_name] = False # Reset pending flag on error

    else:
        # --- Throttle Active: Reschedule this check function ---
        remaining_wait = min_interval - time_since_last_update
        # # # print("f:DEBUG (debounce_check): Throttled {bounds_name}. Rescheduling check in {remaining_wait:.2f}s") # DEBUG
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


# --- Core Update Function ---

def run_sdf_update(scene: bpy.types.Scene, bounds_name: str, trigger_state: dict, is_viewport_update: bool = False):
    """
    Performs the core SDF generation and mesh update for a specific bounds hierarchy.
    Reads settings and state from the provided `trigger_state`.
    Updates the cache only on success.
    """
    global _updates_pending, _last_update_finish_times, _sdf_update_caches

    # Check libfive availability at runtime
    if not _lf_imported_ok:
         # # # print("FieldForge ERROR (run_update): Libfive not available, cannot update.")
         _updates_pending[bounds_name] = False # Reset pending flag
         return # Cannot proceed

    context = bpy.context
    if not context or not scene or not scene.name:
        print(f"FieldForge ERROR: Invalid context/scene during run_sdf_update for {bounds_name}.")
        if bounds_name in _updates_pending: _updates_pending[bounds_name] = False;
        return # Cannot proceed reliably

    bounds_obj = scene.objects.get(bounds_name)

    # --- Pre-computation Checks ---
    if trigger_state is None: print(f"ERROR: run_sdf_update with None state for {bounds_name}!"); _updates_pending[bounds_name] = False; return
    if not bounds_obj: print(f"ERROR: run_sdf_update for non-existent bounds '{bounds_name}'!"); _updates_pending[bounds_name] = False; return

    update_type = "VIEWPORT" if is_viewport_update else "FINAL"
    start_time = time.time()
    # # # print("f:FieldForge: Starting {update_type} update for {bounds_name}...") # DEBUG

    mesh_update_successful = False
    mesh_generation_error = False
    result_obj = None # Define early for use in finally block

    try:
        # Get settings and state info directly from the trigger_state dictionary
        sdf_settings_state = trigger_state.get('scene_settings')
        bounds_matrix = trigger_state.get('bounds_matrix') # Matrix at the time state was captured
        # Read the *current* result object name property from the *current* bounds object.
        result_name = bounds_obj.get(constants.SDF_RESULT_OBJ_NAME_PROP)

        if not sdf_settings_state: raise ValueError("SDF settings missing from trigger state")
        if not bounds_matrix: raise ValueError("Bounds matrix missing from trigger state")
        if not result_name: raise ValueError(f"Result object name prop missing from {bounds_name}")

        # 1. Process Hierarchy using the *current* bounds obj as root
        # It's important hierarchy traversal uses the live object state,
        # while transforms/settings use the captured trigger_state.
        final_combined_shape = sdf_logic.process_sdf_hierarchy(bounds_obj, sdf_settings_state)

        if final_combined_shape is None: # Should return lf.emptiness() on error now
            final_combined_shape = lf.emptiness()
            print(f"FieldForge WARN: SDF hierarchy processing returned None for {bounds_name}, using empty.")
            mesh_generation_error = True

        # 2. Define Meshing Region based on the Bounds object's state at trigger time
        b_loc = bounds_matrix.translation
        b_sca_vec = bounds_matrix.to_scale()
        # Define bounds based on scale - might need refinement (e.g., use bounding box diagonal?)
        # Using average scale applied to a unit size (e.g., +/- 1.0 from origin before scaling)
        extent_factor = 1.0 # Base extent for unit cube bounds
        world_extent = max(1e-6, (abs(b_sca_vec.x) + abs(b_sca_vec.y) + abs(b_sca_vec.z)) / 3.0) * extent_factor
        xyz_min = tuple(b_loc[i] - world_extent for i in range(3))
        xyz_max = tuple(b_loc[i] + world_extent for i in range(3))
        # # # print("f:DEBUG: Meshing region for {bounds_name}: Min={xyz_min}, Max={xyz_max}") # DEBUG

        # 3. Select Resolution based on update type and settings from trigger_state
        resolution = sdf_settings_state.get("sdf_final_resolution", 30)
        if is_viewport_update:
            resolution = sdf_settings_state.get("sdf_viewport_resolution", 10)
        resolution = max(3, resolution) # Ensure minimum resolution

        # 4. Generate Mesh using libfive
        mesh_data = None
        is_not_empty = final_combined_shape is not None and final_combined_shape is not lf.emptiness()
        if not mesh_generation_error and is_not_empty:
            gen_start_time = time.time()
            try:
                mesh_data = final_combined_shape.get_mesh(xyz_min=xyz_min, xyz_max=xyz_max, resolution=resolution)
                gen_duration = time.time() - gen_start_time
                # # # print("f:FieldForge: Mesh gen took {gen_duration:.3f}s for {bounds_name} (Res: {resolution})") # DEBUG
                if not mesh_data or not mesh_data[0]: # Check if get_mesh returned empty
                     # # # print("f:FieldForge INFO: Mesh gen resulted in empty mesh for {bounds_name}.") # DEBUG
                     mesh_data = None # Treat as no mesh data
            except Exception as e:
                print(f"FieldForge Error: libfive mesh generation failed for {bounds_name}: {e}")
                mesh_generation_error = True
                mesh_data = None

        # 5. Find or Create Result Object
        result_obj = utils.find_result_object(context, result_name)
        if not result_obj:
            if utils.get_bounds_setting(bounds_obj, "sdf_create_result_object"):
                print(f"FieldForge INFO: Result object '{result_name}' not found, creating...") # INFO
                try:
                    mesh_data_b = bpy.data.meshes.new(name=result_name + "_Mesh")
                    result_obj = bpy.data.objects.new(result_name, mesh_data_b)
                    context.collection.objects.link(result_obj) # Link to active collection
                    result_obj.matrix_world = Matrix.Identity(4) # Place at origin
                    result_obj.hide_select = True # Make non-selectable by default
                except Exception as e:
                    mesh_generation_error = True
                    raise ValueError(f"Failed to create result object {result_name}: {e}") from e
            else: # No result obj and creation disabled
                 if not mesh_generation_error and mesh_data: # We generated data but have nowhere to put it
                      mesh_generation_error = True
                      print(f"FieldForge ERROR: Result obj '{result_name}' not found & auto-create disabled for {bounds_name}, but mesh data was generated.")
                 # Else: No result obj, no error, no mesh data -> considered success (empty result expected)
                 mesh_update_successful = True

        # 6. Update Mesh Data
        if result_obj and not mesh_generation_error:
            if result_obj.type != 'MESH':
                raise TypeError(f"Target '{result_name}' is not a Mesh (type: {result_obj.type}).")

            mesh = result_obj.data
            if mesh_data: # We have valid verts/tris
                if mesh.vertices or mesh.polygons or mesh.loops: mesh.clear_geometry() # Clear previous
                try:
                    mesh.from_pydata(mesh_data[0], [], mesh_data[1]) # Verts, Edges (empty), Faces
                    mesh.update() # Recalculate normals, bounds
                    mesh_update_successful = True
                    # # # print("f:FieldForge: Applied mesh data to '{result_name}'.") # DEBUG
                except Exception as e:
                    print(f"FieldForge ERROR: Applying mesh data to '{result_name}' failed: {e}")
                    if mesh.vertices: mesh.clear_geometry() # Attempt cleanup
                    mesh_update_successful = False
            else: # No mesh data generated (e.g., empty shape or error)
                if mesh.vertices or mesh.polygons or mesh.loops: # Clear existing geometry if needed
                    # # # print("f:FieldForge: Clearing geometry for '{result_name}' (empty result).") # DEBUG
                    mesh.clear_geometry()
                    mesh.update()
                mesh_update_successful = True # Considered success (empty result applied)

    except Exception as e:
         print(f"FieldForge ERROR during {update_type} update for {bounds_name}: {e}")
         traceback.print_exc()
         mesh_generation_error = True
         mesh_update_successful = False
         # Attempt to clear result mesh geometry on error
         try:
             if result_obj and result_obj.type == 'MESH' and (result_obj.data.vertices or result_obj.data.polygons):
                 result_obj.data.clear_geometry(); result_obj.data.update()
         except Exception: pass

    # --- Final Steps (Cache, Time, Flags) ---
    finally:
        if not mesh_generation_error and mesh_update_successful:
            update_sdf_cache(trigger_state, bounds_name) # Update cache ONLY on success
        # Record finish time regardless of success for throttling
        _last_update_finish_times[bounds_name] = time.time()
        # Reset pending flag for this specific bounds object
        _updates_pending[bounds_name] = False
        end_time = time.time()
        # # # print("f:FieldForge: Finished {update_type} for {bounds_name} in {end_time - start_time:.3f}s (Success: {mesh_update_successful})") # DEBUG


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

    updated_bounds_names = set() # Track which Bounds hierarchies are affected by relevant changes
    needs_visual_redraw = False # Flag if custom visuals need redraw (transform change)

    for update in depsgraph.updates:
        updated_obj = None
        if isinstance(update.id, bpy.types.Object):
            try: updated_obj = update.id.evaluated_get(depsgraph) if depsgraph else update.id # Get evaluated
            except (ReferenceError, AttributeError): continue # Object might be gone or invalid
            if not updated_obj: continue
        elif isinstance(update.id, bpy.types.Scene) and update.is_updated_geometry:
            # If scene geometry generally updated, might need to recheck all bounds? Risky.
            # For now, focus on object updates.
            pass

        if updated_obj:
            # Find the root Bounds object for the updated object
            root_bounds = utils.find_parent_bounds(updated_obj)
            is_bounds_itself = updated_obj.get(constants.SDF_BOUNDS_MARKER, False) if root_bounds is None else False # Check if it IS the bounds
            is_source = utils.is_sdf_source(updated_obj)

            if root_bounds or is_bounds_itself:
                 bounds_to_check = root_bounds if root_bounds else updated_obj # Target the bounds obj
                 # --- Check conditions that require SDF Recompute ---
                 recompute_needed = False
                 # 1. Transform changed on bounds OR a source within its hierarchy
                 if update.is_updated_transform and (is_bounds_itself or is_source):
                     recompute_needed = True
                     if is_source: needs_visual_redraw = True # Source transform change needs visual redraw
                 # 2. Geometry changed (relevant for modifier results, maybe less direct for SDF)
                 # Check this carefully - might trigger too often. Maybe only if sdf_show_source_empties is off?
                 # if update.is_updated_geometry: recompute_needed = True
                 # 3. Custom property change (not detected by depsgraph) - requires manual trigger or UI hook

                 if recompute_needed:
                      updated_bounds_names.add(bounds_to_check.name)

    # Trigger the check function for each affected bounds hierarchy
    if updated_bounds_names:
        # # print("f:DEBUG (Depsgraph): Changes detected affecting bounds: {updated_bounds_names}") # DEBUG
        current_scene = getattr(context, 'scene', None)
        if current_scene: # Check scene exists before passing
             for bounds_name in updated_bounds_names:
                try:
                    # Use timer to avoid potential depsgraph recursion issues
                    bpy.app.timers.register(lambda scn=current_scene, name=bounds_name: check_and_trigger_update(scn, name, "depsgraph"), first_interval=0.0)
                except Exception as e: print(f"FieldForge ERROR: Failed schedule check_trigger from depsgraph for {bounds_name}: {e}")
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

    # # print("FieldForge: Running initial update check for existing bounds...")
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
    global _debounce_timers, _last_trigger_states, _updates_pending, _last_update_finish_times, _sdf_update_caches
    # # print("FieldForge: Clearing timers and update state...")
    if bpy.app.timers: # Check if timers module is still valid
        for bounds_name in list(_debounce_timers.keys()):
             cancel_debounce_timer(bounds_name) # Use existing cancel function
    _debounce_timers.clear()
    _last_trigger_states.clear()
    _updates_pending.clear()
    _last_update_finish_times.clear()
    _sdf_update_caches.clear()
    # # print("FieldForge: Timers and state cleared.")