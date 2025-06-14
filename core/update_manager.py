"""
Manages the automatic update process for FieldForge SDF systems.
Includes debouncing, throttling, triggering mesh regeneration,
and the Blender dependency graph handler.
"""

import bpy
import time
import math
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

_link_dependents_cache = {}
_reverse_link_cache = {}


def clear_link_caches(): # Call from clear_timers_and_state
    global _link_dependents_cache, _reverse_link_cache
    _link_dependents_cache.clear()
    _reverse_link_cache.clear()

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
        return

    # Check the auto-update setting ON THE BOUNDS OBJECT
    if not utils.get_bounds_setting(bounds_obj, "sdf_auto_update"):
        return # Auto update disabled for this system

    # Don't re-trigger if an update is already pending/running for this bounds
    if _updates_pending.get(bounds_name, False):
        return

    # Get the current state ONLY if necessary checks pass
    current_state = state.get_current_sdf_state(context, bounds_obj)
    if not current_state: # Handle case where state gathering fails
        print(f"FieldForge WARN (check_trigger): Could not get current state for {bounds_name}.")
        return

    # Compare current state to the cached state for this specific bounds
    cached_state = _sdf_update_caches.get(bounds_name)
    if state.has_state_changed(current_state, cached_state): # Pass cached state directly
        # State has changed, schedule a new debounce timer
        schedule_new_debounce_timer(scene, bounds_name, current_state)
        # Trigger redraw for custom visuals (handled in drawing.py)
        # Consider importing and calling drawing.tag_redraw_all_view3d() here if needed
        # import drawing; drawing.tag_redraw_all_view3d() # Requires careful import handling


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

    mesh_update_successful = False
    mesh_generation_error = False
    result_obj = None # Define early for use in finally block

    try:
        sdf_settings_from_bounds = trigger_state.get('scene_settings')
        bounds_matrix_at_trigger = trigger_state.get('bounds_matrix')
        result_name = bounds_obj.get(constants.SDF_RESULT_OBJ_NAME_PROP)

        if not sdf_settings_from_bounds: raise ValueError("SDF bounds settings missing from trigger state")
        if not bounds_matrix_at_trigger: raise ValueError("Bounds matrix missing from trigger state")

        # 1. Process Hierarchy using the *current* bounds_obj as root.
        #    Pass sdf_settings_from_bounds as the `bounds_settings` argument
        #    to sdf_logic.process_sdf_hierarchy.
        final_combined_shape = sdf_logic.process_sdf_hierarchy(bounds_obj, sdf_settings_from_bounds)

        if final_combined_shape is None:
            final_combined_shape = lf.emptiness()
            print(f"FieldForge WARN: SDF hierarchy processing returned None for {bounds_name}, using empty.")
            mesh_generation_error = True

        # 2. Define Meshing Region based on the Bounds object's state at trigger time
        #    Use bounds_matrix_at_trigger here for consistency with the state that triggered the update.
        b_loc = bounds_matrix_at_trigger.translation
        b_sca_vec = bounds_matrix_at_trigger.to_scale()
        extent_factor = 1.0 
        world_extent_avg = max(1e-6, (abs(b_sca_vec.x) + abs(b_sca_vec.y) + abs(b_sca_vec.z)) / 3.0) * extent_factor
        
        # More robust way to define bounds using actual scale components
        # Ensure bounds are not inverted if scale is negative.
        # The region should be defined by min/max corners in world space.
        # If bounds_obj itself is scaled, its local unit cube (-1 to 1 on each axis)
        # defines the meshing region in its local space. We need to transform these
        # 8 corner points to world space using bounds_matrix_at_trigger and find the min/max.
        
        local_corners = [
            Vector((-1, -1, -1)), Vector((1, -1, -1)),
            Vector((-1,  1, -1)), Vector((1,  1, -1)),
            Vector((-1, -1,  1)), Vector((1, -1,  1)),
            Vector((-1,  1,  1)), Vector((1,  1,  1)),
        ]
        world_corners = [(bounds_matrix_at_trigger @ corner.to_4d()).xyz for corner in local_corners]

        min_x = min(c.x for c in world_corners)
        max_x = max(c.x for c in world_corners)
        min_y = min(c.y for c in world_corners)
        max_y = max(c.y for c in world_corners)
        min_z = min(c.z for c in world_corners)
        max_z = max(c.z for c in world_corners)

        xyz_min = (min_x, min_y, min_z)
        xyz_max = (max_x, max_y, max_z)


        # 3. Select Resolution based on update type and settings from sdf_settings_from_bounds
        resolution = sdf_settings_from_bounds.get("sdf_final_resolution", 30)
        if is_viewport_update:
            resolution = sdf_settings_from_bounds.get("sdf_viewport_resolution", 10)
        resolution = max(3, int(resolution))

        # 4. Generate Mesh using libfive
        mesh_data = None
        is_not_empty = final_combined_shape is not None and final_combined_shape is not lf.emptiness()
        if not mesh_generation_error and is_not_empty:
            gen_start_time = time.time()
            try:
                mesh_data = final_combined_shape.get_mesh(xyz_min=xyz_min, xyz_max=xyz_max, resolution=resolution)
                if not mesh_data or not mesh_data[0]: # Check if get_mesh returned empty
                     mesh_data = None # Treat as no mesh data
            except Exception as e:
                print(f"FieldForge Error: libfive mesh generation failed for {bounds_name}: {e}")
                mesh_generation_error = True
                mesh_data = None

        # 5. Find or Create Result Object
        if not result_name and utils.get_bounds_setting(bounds_obj, "sdf_create_result_object"):
            # Auto-generate a result name if empty and creation is allowed
            # This part might need a more robust naming if bounds_name isn't unique enough as a base
            base_for_result = bounds_name.replace("_Bounds", "") or "SDF_System"
            unique_result_name_base = base_for_result + "_Result"
            i = 1
            result_name_candidate = unique_result_name_base
            while result_name_candidate in scene.objects:
                result_name_candidate = f"{unique_result_name_base}.{i:03d}"
                i += 1
            result_name = result_name_candidate
            bounds_obj[constants.SDF_RESULT_OBJ_NAME_PROP] = result_name # Store the new name
            print(f"FieldForge: Auto-generated result object name '{result_name}' for {bounds_name}")


        result_obj = utils.find_result_object(context, result_name) if result_name else None
        if not result_obj and result_name: # Name exists but object doesn't
            if utils.get_bounds_setting(bounds_obj, "sdf_create_result_object"):
                try:
                    new_mesh_bdata = bpy.data.meshes.new(name=result_name + "_Mesh")
                    result_obj = bpy.data.objects.new(result_name, new_mesh_bdata)
                    link_collection = bounds_obj.users_collection[0] if bounds_obj.users_collection else scene.collection
                    link_collection.objects.link(result_obj)
                    result_obj.matrix_world = Matrix.Identity(4) 
                    result_obj.hide_select = True 
                except Exception as e_create:
                    mesh_generation_error = True # Mark error if creation fails
                    print(f"FieldForge Error: Failed to create result object {result_name}: {e_create}")
                    # Don't raise here, let finally block handle pending flag
            else: 
                 if not mesh_generation_error and mesh_data:
                      mesh_generation_error = True # Error if data generated but no place to put it
                      print(f"FieldForge ERROR: Result obj '{result_name}' not found & auto-create disabled for {bounds_name}, but mesh data was generated.")
                 # If no data and no obj, this is fine (empty result for an empty name slot)
                 mesh_update_successful = True # Considered success if no data and no object creation expected

        # 6. Update Mesh Data
        if result_obj and not mesh_generation_error: # Only proceed if obj exists and no prior error
            if result_obj.type != 'MESH':
                # This should ideally not happen if we control creation.
                print(f"FieldForge ERROR: Target '{result_name}' is not a Mesh (type: {result_obj.type}). Cannot update.")
                mesh_generation_error = True # Mark as error
            else:
                mesh_bdata = result_obj.data # bpy.types.Mesh
                if mesh_data: 
                    if mesh_bdata.vertices or mesh_bdata.polygons or mesh_bdata.loops: mesh_bdata.clear_geometry()
                    try:
                        mesh_bdata.from_pydata(mesh_data[0], [], mesh_data[1]) 
                        mesh_bdata.update() 
                        mesh_update_successful = True
                    except Exception as e_apply:
                        print(f"FieldForge ERROR: Applying mesh data to '{result_name}' failed: {e_apply}")
                        if mesh_bdata.vertices: mesh_bdata.clear_geometry(); mesh_bdata.update()
                        mesh_update_successful = False # Explicitly false
                        mesh_generation_error = True # This is also a generation/application error
                else: # No mesh data from libfive (e.g., empty shape or earlier error)
                    if mesh_bdata.vertices or mesh_bdata.polygons or mesh_bdata.loops: 
                        mesh_bdata.clear_geometry()
                        mesh_bdata.update()
                    mesh_update_successful = True # Success: empty result applied correctly
                if mesh_update_successful and result_obj.data and hasattr(result_obj.data, 'polygons'):
                    try:
                        if len(result_obj.data.polygons) > 0: # Only if there are polygons
                            for poly in result_obj.data.polygons:
                                poly.use_smooth = True
                            result_obj.data.update() # Update mesh after changing polygon smooth flags
                    except Exception as e_smooth:
                        print(f"FieldForge WARN: Could not apply smooth shading to {result_name}: {e_smooth}")
                
                # --- Apply Smooth Shading & Auto Smooth Angle via Modifier ---
                if mesh_update_successful and result_obj and result_obj.type == 'MESH' and result_obj.data:
                    mesh_data_block = result_obj.data
                    addon_modifier_name = "FieldForge_Smooth_By_Angle"
                    angle_input_identifier = "Input_1" 

                    try:
                        if len(mesh_data_block.polygons) > 0:
                            for poly in mesh_data_block.polygons:
                                poly.use_smooth = True

                        auto_smooth_angle_deg = sdf_settings_from_bounds.get(
                            "sdf_result_auto_smooth_angle", 
                            constants.DEFAULT_SETTINGS["sdf_result_auto_smooth_angle"]
                        )
                        auto_smooth_angle_deg = float(auto_smooth_angle_deg)
                        auto_smooth_angle_rad = math.radians(auto_smooth_angle_deg)
                        existing_modifier = result_obj.modifiers.get(addon_modifier_name)
                        
                        if existing_modifier and existing_modifier.type == 'NODES':
                            try:
                                if angle_input_identifier in existing_modifier:
                                    if abs(existing_modifier[angle_input_identifier] - auto_smooth_angle_rad) > 1e-5:
                                        existing_modifier[angle_input_identifier] = auto_smooth_angle_rad
                                else:
                                    print(f"FieldForge WARN: Input '{angle_input_identifier}' not found on existing modifier '{addon_modifier_name}' for {result_name}. Recreating.")
                                    bpy.ops.object.modifier_remove({'object': result_obj}, modifier=existing_modifier.name)
                                    existing_modifier = None
                            except (KeyError, TypeError, SystemError) as e_mod_update:
                                print(f"FieldForge WARN: Failed to update '{angle_input_identifier}' on '{addon_modifier_name}' for {result_name}. Recreating. Error: {e_mod_update}")
                                bpy.ops.object.modifier_remove({'object': result_obj}, modifier=existing_modifier.name)
                                existing_modifier = None 

                        if not existing_modifier:
                            current_active = context.view_layer.objects.active
                            current_selected_names = [o.name for o in context.selected_objects]
                            bpy.ops.object.select_all(action='DESELECT')
                            result_obj.select_set(True); context.view_layer.objects.active = result_obj
                            try:
                                bpy.ops.object.modifier_add_node_group(
                                    asset_library_type='ESSENTIALS',
                                    asset_library_identifier="Essentials", 
                                    relative_asset_identifier="geometry_nodes/smooth_by_angle.blend/NodeTree/Smooth by Angle"
                                )
                                new_modifier = result_obj.modifiers[-1]
                                new_modifier.name = addon_modifier_name
                                if angle_input_identifier in new_modifier:
                                    new_modifier[angle_input_identifier] = auto_smooth_angle_rad
                                else:
                                    print(f"FieldForge ERROR: Newly added 'Smooth by Angle' for {result_name} no input '{angle_input_identifier}'.")
                            except RuntimeError as e_mod_add:
                                print(f"FieldForge ERROR: Failed to add 'Smooth by Angle' modifier to {result_name}: {e_mod_add}")
                            finally:
                                if context.view_layer.objects.active == result_obj: result_obj.select_set(False)
                                for name in current_selected_names:
                                    obj_to_reselect = context.scene.objects.get(name)
                                    if obj_to_reselect:
                                        try: obj_to_reselect.select_set(True)
                                        except ReferenceError: pass 
                                try: 
                                    if current_active and current_active.name in context.scene.objects : 
                                        context.view_layer.objects.active = current_active
                                    elif context.selected_objects : 
                                        context.view_layer.objects.active = context.selected_objects[0]
                                except ReferenceError: pass 

                        mesh_data_block.update()

                    except Exception as e_smooth_mod:
                        print(f"FieldForge WARN: Broader error applying smooth shading/modifier to {result_name}: {e_smooth_mod}")

                if mesh_update_successful and result_obj: # Ensure result_obj exists
                    try:
                        mat_name_to_assign = sdf_settings_from_bounds.get("sdf_result_material_name", "")
                        if mat_name_to_assign: # If a material name is specified
                            material_to_assign = bpy.data.materials.get(mat_name_to_assign)
                            if material_to_assign:
                                if result_obj.data.materials: # If there are material slots
                                    if not result_obj.data.materials[0] or result_obj.data.materials[0].name != material_to_assign.name :
                                        result_obj.data.materials[0] = material_to_assign
                                else: # No material slots, append one
                                    result_obj.data.materials.append(material_to_assign)
                            else:
                                # Material name specified but not found, clear if a different one was assigned
                                if result_obj.data.materials and result_obj.data.materials[0] is not None:
                                    print(f"FieldForge WARN: Material '{mat_name_to_assign}' not found for {result_name}. Clearing existing material.")
                                    result_obj.data.materials.clear() # Or result_obj.data.materials[0] = None if you want to keep the slot
                                # If no material was previously assigned, do nothing if new one not found.
                        else: # No material name specified, ensure no material is assigned
                            if result_obj.data.materials:
                                result_obj.data.materials.clear() # Remove all materials from the object's mesh data
                    except Exception as e_mat: # pragma: no cover
                        print(f"FieldForge WARN: Could not assign material to {result_name}: {e_mat}")

    except Exception as e_outer:
         print(f"FieldForge ERROR during {update_type} update for {bounds_name}: {type(e_outer).__name__} - {e_outer}")
         mesh_generation_error = True # Outer loop error
         mesh_update_successful = False
         try:
             if result_obj and result_obj.type == 'MESH' and result_obj.data and (result_obj.data.vertices or result_obj.data.polygons):
                 result_obj.data.clear_geometry(); result_obj.data.update()
         except Exception: pass

    finally:
        if not mesh_generation_error and mesh_update_successful:
            update_sdf_cache(trigger_state, bounds_name) 
        
        _last_update_finish_times[bounds_name] = time.time()
        _updates_pending[bounds_name] = False

def _update_linker_caches(linker_obj: bpy.types.Object, new_target_name: str | None, linker_parent_bounds_name: str | None):
    """Manages the _link_dependents_cache and _reverse_link_cache."""
    global _link_dependents_cache, _reverse_link_cache
    
    linker_name = linker_obj.name

    # 1. Clean up old dependencies for this linker_name
    old_target_name_for_linker = _reverse_link_cache.pop(linker_name, None)
    if old_target_name_for_linker and old_target_name_for_linker in _link_dependents_cache:
        if linker_parent_bounds_name in _link_dependents_cache[old_target_name_for_linker]:
            _link_dependents_cache[old_target_name_for_linker].remove(linker_parent_bounds_name)
            if not _link_dependents_cache[old_target_name_for_linker]: # Is set empty?
                del _link_dependents_cache[old_target_name_for_linker]

    # 2. Add new dependency if new_target_name and linker_parent_bounds_name are valid
    if new_target_name and linker_parent_bounds_name:
        _link_dependents_cache.setdefault(new_target_name, set()).add(linker_parent_bounds_name)
        _reverse_link_cache[linker_name] = new_target_name
    elif linker_name in _reverse_link_cache: # If new_target_name is None, ensure linker is removed from reverse cache
        del _reverse_link_cache[linker_name]

# This function would be called from state.py
def register_link_dependency(linker_obj: bpy.types.Object, effective_target_obj: bpy.types.Object | None, linker_parent_bounds: bpy.types.Object | None):
    if not linker_obj:
        return

    linker_parent_bounds_name = linker_parent_bounds.name if linker_parent_bounds else None
    
    current_link_target_prop_val = linker_obj.get(constants.SDF_LINK_TARGET_NAME_PROP, "")

    if effective_target_obj and effective_target_obj != linker_obj and current_link_target_prop_val == effective_target_obj.name:
        # Link is valid and points to effective_target_obj
        _update_linker_caches(linker_obj, effective_target_obj.name, linker_parent_bounds_name)
    else:
        # Link is invalid, empty, or points to self, or prop val doesn't match effective (e.g. incompatible)
        _update_linker_caches(linker_obj, None, linker_parent_bounds_name)

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
            if updated_obj.name in _link_dependents_cache:
                for dependent_bounds_name in _link_dependents_cache[updated_obj.name]:
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
    if bpy.app.timers: # Check if timers module is still valid
        for bounds_name in list(_debounce_timers.keys()):
             cancel_debounce_timer(bounds_name) # Use existing cancel function
    _debounce_timers.clear()
    _last_trigger_states.clear()
    _updates_pending.clear()
    _last_update_finish_times.clear()
    _sdf_update_caches.clear()