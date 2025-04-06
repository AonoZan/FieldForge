# -*- coding: utf-8 -*-
"""
FieldForge Addon for Blender: Create and manage dynamic Signed Distance Function
(SDF) shapes using libfive, featuring hierarchical blending and per-system controls.
"""

bl_info = {
    "name": "FieldForge",
    "author": "Your Name & libfive Team",
    "version": (0, 5, 0),
    "blender": (4, 4, 0),
    "location": "View3D > Add > Mesh > Field Forge SDF | Object Properties",
    "description": "Adds and manages dynamic SDF shapes using libfive with hierarchical blending",
    "warning": "Requires compiled libfive libraries.",
    "doc_url": "",
    "category": "Add Mesh",
}

import bpy
import os
import sys
import time
from mathutils import Vector, Matrix
from bpy.types import Operator, Menu, Panel, PropertyGroup
from bpy.props import (
    FloatVectorProperty, FloatProperty, IntProperty, PointerProperty,
    StringProperty, EnumProperty, BoolProperty
)
from bpy.app.handlers import persistent

# --- Libfive Import Handling ---
addon_dir = os.path.dirname(os.path.realpath(__file__))
libfive_python_dir = os.path.join(addon_dir)

# Ensure the addon directory is in the path for libfive discovery
if libfive_python_dir not in sys.path:
    sys.path.append(libfive_python_dir)

libfive_available = False
lf = None
ffi = None
try:
    # Attempt to import libfive components
    import libfive.ffi as ffi
    import libfive.shape # Import shape early if stdlib depends on it
    import libfive.stdlib as lf
    # Basic verification check
    if hasattr(lf, 'sphere') and hasattr(ffi.lib, 'libfive_tree_const'):
        libfive_available = True
        # print("FieldForge: Successfully imported and verified libfive.") # Keep silent in final
    else:
         raise ImportError("Core or stdlib function check failed")

except ImportError as e:
    print(f"FieldForge: Error importing libfive: {e}")
    # Provide guidance on library paths
    core_lib_path = os.path.join(addon_dir, "libfive", "src", f"libfive.{'dll' if sys.platform == 'win32' else 'dylib' if sys.platform == 'darwin' else 'so'}")
    stdlib_lib_path = os.path.join(addon_dir, "libfive", "stdlib", f"libfive-stdlib.{'dll' if sys.platform == 'win32' else 'dylib' if sys.platform == 'darwin' else 'so'}")
    print(f"FieldForge: Ensure compiled libfive libraries exist, e.g.:")
    print(f"  - Core: {core_lib_path}")
    print(f"  - Stdlib: {stdlib_lib_path}")
    print(f"FieldForge: Addon requires libfive. Dynamic functionality disabled.")
except Exception as e:
    print(f"FieldForge: An unexpected error occurred during libfive import: {e}")
    print(f"FieldForge: Dynamic functionality disabled.")


# --- Constants ---
SDF_BOUNDS_MARKER = "is_sdf_bounds"           # Custom property key for Bounds objects
SDF_RESULT_OBJ_NAME_PROP = "sdf_result_object_name" # Custom prop key storing result obj name
SDF_PROPERTY_MARKER = "is_sdf_object"         # Custom property key for source Empty objects
CACHE_PRECISION = 1e-5                      # Tolerance for float comparisons in caching


# --- Global State Dictionaries (Keys are bounds_obj.name) ---
_debounce_timers = {}           # Stores active bpy.app.timer references for viewport updates
_last_trigger_states = {}       # Caches the state that triggered the last debounce timer start
_updates_pending = {}           # Flags indicating an update is scheduled or running
_last_update_finish_times = {}  # Stores the time the last update finished (for throttling)
_sdf_update_caches = {}         # Caches the last known state used for a successful update


# --- Default Settings (Applied to new Bounds objects) ---
DEFAULT_SETTINGS = {
    "sdf_final_resolution": 30,     # Resolution for manual/render updates
    "sdf_viewport_resolution": 5,  # Resolution for automatic viewport previews
    "sdf_realtime_update_delay": 0.5, # Inactivity time before viewport update attempt
    "sdf_minimum_update_interval": 1.0, # Minimum time between end of updates
    "sdf_global_blend_factor": 0.1,   # Default blend factor for direct children of Bounds
    "sdf_auto_update": True,        # Enable/disable automatic viewport updates
    "sdf_show_source_empties": True, # Visibility toggle for source Empties
    "sdf_create_result_object": True, # Auto-create result mesh if missing during update
}


# --- Helper Functions ---

def get_all_bounds_objects(context):
    """ Generator yielding all SDF Bounds objects in the current scene """
    for obj in context.scene.objects:
        if obj.get(SDF_BOUNDS_MARKER, False):
            yield obj

def find_result_object(context, result_name):
    """ Finds a result object by its name """
    if not result_name: return None
    return context.scene.objects.get(result_name)

def find_parent_bounds(start_obj):
    """ Traverses up the hierarchy to find the root SDF Bounds object """
    obj = start_obj
    count = 0 # Safety break for deep hierarchies or cycles
    while obj and count < 100:
        if obj.get(SDF_BOUNDS_MARKER, False):
            return obj
        obj = obj.parent
        count += 1
    return None # Not part of a known bounds hierarchy

def is_sdf_source(obj):
    """ Checks if an object is configured as an SDF source Empty """
    return obj and obj.type == 'EMPTY' and obj.get(SDF_PROPERTY_MARKER, False)

def update_empty_visibility(scene):
    """ Hides or shows source empties based on their root Bounds setting """
    if not libfive_available: return
    context = bpy.context
    processed_objects = set() # Track objects globally to avoid re-processing in nested Bounds

    for bounds_obj in get_all_bounds_objects(context):
        bounds_name = bounds_obj.name
        if bounds_name in processed_objects: continue
        processed_objects.add(bounds_name)

        # Read the visibility setting directly from the Bounds object
        show = bounds_obj.get("sdf_show_source_empties", True)

        # Traverse hierarchy downwards from this Bounds object
        q = [bounds_obj]
        visited_in_hierarchy = {bounds_name} # Track within this specific hierarchy traversal

        while q:
            parent_obj = q.pop(0)
            for child_obj in parent_obj.children:
                 child_name = child_obj.name
                 # Process only if not visited in this hierarchy and not globally processed
                 if child_name not in visited_in_hierarchy and child_name not in processed_objects:
                    visited_in_hierarchy.add(child_name)
                    processed_objects.add(child_name) # Mark globally once processed

                    if is_sdf_source(child_obj):
                        try:
                            # Ensure object still exists before accessing properties
                            if child_obj.name in scene.objects:
                                 child_obj.hide_viewport = not show
                                 child_obj.hide_render = not show
                        except ReferenceError: pass # Object might have been deleted during iteration

                        # Add child to queue only if it still exists
                        if child_obj and child_obj.name in scene.objects:
                            q.append(child_obj)
                    elif child_obj and child_obj.name in scene.objects:
                         # If child is not a source, still traverse its children
                         q.append(child_obj)

def get_bounds_setting(bounds_obj, setting_key):
    """ Safely retrieves a setting from a bounds object, falling back to defaults """
    if not bounds_obj:
        return DEFAULT_SETTINGS.get(setting_key)
    # Use .get() on the object itself (accessing custom properties)
    return bounds_obj.get(setting_key, DEFAULT_SETTINGS.get(setting_key))

def compare_matrices(mat1, mat2, tolerance=CACHE_PRECISION):
    """ Compare two 4x4 matrices element-wise with a tolerance for floats. """
    if mat1 is None or mat2 is None: return mat1 is mat2
    for i in range(4):
        for j in range(4):
            if abs(mat1[i][j] - mat2[i][j]) > tolerance:
                return False
    return True

def compare_dicts(dict1, dict2, tolerance=CACHE_PRECISION):
    """ Compare dictionaries (shallow), checking floats/vectors/matrices with tolerance. """
    if dict1 is None or dict2 is None: return dict1 is dict2
    if set(dict1.keys()) != set(dict2.keys()): return False

    for key, val1 in dict1.items():
        val2 = dict2.get(key) # Use get() for safety

        if isinstance(val1, float):
            if not isinstance(val2, (float, int)) or abs(val1 - val2) > tolerance: return False
        elif isinstance(val1, int):
            if not isinstance(val2, int) or val1 != val2: return False
        elif isinstance(val1, Matrix):
            if not isinstance(val2, Matrix) or not compare_matrices(val1, val2, tolerance): return False
        elif isinstance(val1, Vector):
            if not isinstance(val2, Vector) or len(val1) != len(val2): return False
            for i in range(len(val1)):
                if abs(val1[i] - val2[i]) > tolerance: return False
        # Handle other common types directly
        elif val1 != val2: # Direct comparison for bool, string, etc.
            return False
    return True

def reconstruct_shape(obj):
    """
    Reconstructs a UNIT libfive shape based on the object's 'sdf_type' property.
    Scaling and transformation are handled separately via the object's matrix.
    """
    if not libfive_available or not obj: return None

    sdf_type = obj.get("sdf_type", "")
    shape = None
    unit_radius = 0.5 # Standard radius for shapes like cylinder/cone base
    unit_height = 1.0 # Standard height for shapes like cylinder/cone

    try:
        if sdf_type == "cube":
            # Unit cube centered at origin (size 1x1x1 after transform scaling)
            shape = lf.cube_centered((1.0, 1.0, 1.0))
        elif sdf_type == "sphere":
            # Unit sphere centered at origin (radius 0.5 after transform scaling)
            shape = lf.sphere(0.5)
        elif sdf_type == "cylinder":
            # Unit cylinder along Z, radius 0.5, height 1.0, centered at origin
            shape = lf.cylinder_z(unit_radius, unit_height, base=(0, 0, -unit_height / 2.0))
        elif sdf_type == "cone":
             # Unit cone along Z, radius 0.5, height 1.0, centered at origin
            shape = lf.cone_z(unit_radius, unit_height, base=(0, 0, -unit_height / 2.0))
        elif sdf_type == "torus":
            # Unit torus, example: Major Radius 0.35, Minor Radius 0.15
            major_r = 0.35
            minor_r = 0.15
            shape = lf.torus_z(major_r, minor_r, center=(0,0,0))
        elif sdf_type == "rounded_box":
            # Base shape is a 1x1x1 cube centered at origin
            # Rounding radius is an absolute value read from the object property
            round_radius = obj.get("sdf_round_radius", 0.1)
            half_size = 0.5
            corner_a = (-half_size, -half_size, -half_size)
            corner_b = ( half_size,  half_size,  half_size)
            shape = lf.rounded_box(corner_a, corner_b, round_radius)
        else:
            # Return emptiness for unknown types to avoid errors down the line
            return lf.emptiness()

    except Exception as e:
        print(f"FieldForge: Error reconstructing unit shape for {obj.name} ({sdf_type}): {e}")
        return None # Or lf.emptiness()? None indicates a failure.

    return shape

def apply_blender_transform_to_sdf(shape, obj_matrix_world_inv):
    """ Applies Blender object's inverted world transform to a libfive shape using remap. """
    if shape is None or obj_matrix_world_inv is None:
        return None # Cannot transform None

    # Get libfive's symbolic world coordinate variables
    X = lf.Shape.X()
    Y = lf.Shape.Y()
    Z = lf.Shape.Z()

    # Calculate the remapped coordinates using the inverse matrix components
    mat_inv = obj_matrix_world_inv
    try:
        # Standard matrix multiplication: new_coord = mat_inv * old_coord
        x_p = mat_inv[0][0] * X + mat_inv[0][1] * Y + mat_inv[0][2] * Z + mat_inv[0][3]
        y_p = mat_inv[1][0] * X + mat_inv[1][1] * Y + mat_inv[1][2] * Z + mat_inv[1][3]
        z_p = mat_inv[2][0] * X + mat_inv[2][1] * Y + mat_inv[2][2] * Z + mat_inv[2][3]

        # Apply the coordinate remapping to the shape definition
        transformed_shape = shape.remap(x_p, y_p, z_p)
    except Exception as e:
        print(f"FieldForge: Error during libfive remap for shape: {e}")
        return None

    return transformed_shape

def combine_shapes(shape_a, shape_b, blend_factor):
    """ Combines two libfive shapes using union or blend (blend_expt_unit). """
    # Handle cases where one input might be None (error condition) or represent emptiness
    if shape_b is None: return shape_a
    if shape_a is None: return shape_b
    # TODO: Consider a more robust check for lf.emptiness() if needed

    try:
        if blend_factor > CACHE_PRECISION:
            # Use blend_expt_unit for smooth blending, normalized factor 0-1 expected visually
            # Clamp factor just in case, though UI should limit it.
            clamped_factor = max(0.0, min(1.0, blend_factor))
            return lf.blend_expt_unit(shape_a, shape_b, clamped_factor)
        else:
            # Use sharp union if blend factor is effectively zero
            return lf.union(shape_a, shape_b)
    except Exception as e:
        print(f"FieldForge: Error combining shapes: {e}")
        # Fallback strategy: return the first shape? Or None? Returning first is safer.
        return shape_a

def process_sdf_hierarchy(obj, settings):
    """
    Recursively processes an object and its SDF children, returning the combined libfive shape.
    Applies operations sequentially, skipping invisible objects. Reads blend factors.
    'settings' dict contains settings read from the root Bounds object.
    """
    if not libfive_available or not obj: return None

    # Skip processing if the object is hidden in the viewport (unless it's the Bounds root itself)
    if not obj.visible_get() and not obj.get(SDF_BOUNDS_MARKER, False):
        return lf.emptiness() # Return empty shape for hidden objects

    obj_name = obj.name
    shape_so_far = None
    is_bounds_root = obj.get(SDF_BOUNDS_MARKER, False)

    # 1. Get Base Shape (if the current object is an SDF source)
    if is_sdf_source(obj):
        base_shape = reconstruct_shape(obj)
        if base_shape is not None:
            try:
                # Invert matrix ONCE per object
                obj_matrix_inv = obj.matrix_world.inverted()
                shape_so_far = apply_blender_transform_to_sdf(base_shape, obj_matrix_inv)
            except ValueError: # Matrix inversion failed (e.g., scale is zero)
                print(f"FieldForge Warning: Could not invert matrix for {obj_name}. Skipping object.")
                shape_so_far = lf.emptiness()
            except Exception as e:
                 print(f"FieldForge Error: Transform application failed for {obj_name}: {e}")
                 shape_so_far = lf.emptiness()

    # Initialize with emptiness if no base shape was generated (e.g., it's just a parent/group)
    if shape_so_far is None:
        shape_so_far = lf.emptiness()

    # 2. Determine blend factor for combining CHILDREN of this object
    # The blend factor is defined by the PARENT object (or Bounds global)
    interaction_blend_factor = 0.0
    if is_bounds_root:
        interaction_blend_factor = settings.get("sdf_global_blend_factor", 0.1)
    elif is_sdf_source(obj):
        interaction_blend_factor = obj.get("sdf_child_blend_factor", 0.0)
    # Else (if obj is just a regular Empty used for grouping), blend factor remains 0 (sharp union)

    # 3. Process Children Recursively
    for child in obj.children:
        # Process the child hierarchy recursively
        child_shape = process_sdf_hierarchy(child, settings) # Pass root settings down

        # Combine the child shape if it's valid and not empty
        if child_shape is not None: # Check for None explicitly (processing error)
            # Check if child is marked as negative (subtractive)
            is_child_negative = child.get("sdf_is_negative", False)

            if is_child_negative:
                # Apply difference operation (potentially blended)
                try:
                    # blend_difference uses the same factor logic as blend_expt_unit internally usually
                    shape_so_far = lf.blend_difference(shape_so_far, child_shape, interaction_blend_factor)
                except Exception as e:
                    print(f"FieldForge Error: blend_difference failed for parent {obj_name}, child {child.name}: {e}")
                    # Continue with the next child if difference fails
                    continue
            else:
                # Apply union or blend operation
                shape_so_far = combine_shapes(shape_so_far, child_shape, interaction_blend_factor)

    return shape_so_far


# --- State Gathering and Caching ---

def get_current_sdf_state(context, bounds_obj):
    """ Gathers the current relevant state for a specific Bounds hierarchy. """
    if not bounds_obj: return None
    bounds_name = bounds_obj.name

    current_state = {
        'bounds_name': bounds_name,
        'scene_settings': {}, # Settings specific to this bounds object
        'bounds_matrix': bounds_obj.matrix_world.copy(),
        'source_objects': {}, # Dictionary: {obj_name: {matrix: ..., props: {...}}}
        # Hierarchy structure isn't strictly needed for state comparison if we compare
        # the set of objects and their individual properties/transforms.
    }

    # Read settings from the bounds object itself into the state dictionary
    for key in DEFAULT_SETTINGS.keys():
        current_state['scene_settings'][key] = get_bounds_setting(bounds_obj, key)

    # Traverse hierarchy below this specific bounds object to find visible sources
    q = [bounds_obj]
    visited_in_hierarchy = {bounds_name}

    while q:
        parent_obj = q.pop(0)

        for child_obj in parent_obj.children:
            child_name = child_obj.name
            if child_name in visited_in_hierarchy: continue
            visited_in_hierarchy.add(child_name)

            # Check if the object still exists in the scene
            if child_name not in context.scene.objects: continue
            actual_child_obj = context.scene.objects[child_name]

            # Process only if it's an SDF source AND visible
            if is_sdf_source(actual_child_obj) and actual_child_obj.visible_get():
                 # Gather state for this visible source object
                sdf_type = actual_child_obj.get("sdf_type")
                obj_state = {
                    'matrix': actual_child_obj.matrix_world.copy(),
                    'props': {
                        'sdf_type': sdf_type,
                        'sdf_child_blend_factor': actual_child_obj.get("sdf_child_blend_factor", 0.0),
                        'sdf_is_negative': actual_child_obj.get("sdf_is_negative", False),
                        # Include visibility in state? Not strictly needed if we only gather visible,
                        # but might be useful if visibility itself should trigger an update.
                        # Let's omit for now, as visible_get() check handles entering the block.
                    }
                }
                # Add type-specific properties that affect the shape
                if sdf_type == "rounded_box":
                    obj_state['props']['sdf_round_radius'] = actual_child_obj.get("sdf_round_radius", 0.1)

                current_state['source_objects'][child_name] = obj_state
                # Add to queue to check its children
                q.append(actual_child_obj)

            elif actual_child_obj.children:
                 # If it's not a source but has children, still need to traverse
                 q.append(actual_child_obj)

    return current_state

def has_state_changed(current_state, bounds_name):
    """ Compares the current state to the cached state for a specific bounds object. """
    global _sdf_update_caches
    if not current_state: return False # Cannot compare if no current state provided

    cached_state = _sdf_update_caches.get(bounds_name)
    if not cached_state: return True # No cache exists, so state has effectively changed

    # 1. Compare Settings stored on the bounds object
    if not compare_dicts(current_state['scene_settings'], cached_state.get('scene_settings')):
        # print(f"DEBUG {bounds_name}: Settings changed")
        return True

    # 2. Compare Bounds Matrix
    if not compare_matrices(current_state['bounds_matrix'], cached_state.get('bounds_matrix')):
        # print(f"DEBUG {bounds_name}: Bounds matrix changed")
        return True

    # 3. Compare the set of active source objects (keys of the dict)
    current_source_names = set(current_state['source_objects'].keys())
    cached_source_names = set(cached_state.get('source_objects', {}).keys())
    if current_source_names != cached_source_names:
        # print(f"DEBUG {bounds_name}: Source object set changed")
        return True

    # 4. Compare individual source object states (matrix and properties)
    cached_sources = cached_state.get('source_objects', {})
    for obj_name, current_obj_state in current_state['source_objects'].items():
        cached_obj_state = cached_sources.get(obj_name)
        if not cached_obj_state: return True # Should be caught by key set check, but safe backup

        if not compare_matrices(current_obj_state['matrix'], cached_obj_state.get('matrix')):
            # print(f"DEBUG {bounds_name}: Matrix changed for {obj_name}")
            return True
        if not compare_dicts(current_obj_state['props'], cached_obj_state.get('props')):
            # print(f"DEBUG {bounds_name}: Props changed for {obj_name}")
            return True

    # If all checks pass, the state is considered unchanged
    return False

def update_sdf_cache(new_state, bounds_name):
    """ Updates the cache for a specific bounds object with the new state. """
    global _sdf_update_caches
    if new_state and bounds_name:
        # Store a deep copy? The current state gathering creates copies of matrices.
        # Dictionaries and basic types are fine with shallow copy behavior here.
        _sdf_update_caches[bounds_name] = new_state


# --- Debounce and Throttle Logic (Per Bounds) ---

def check_and_trigger_update(scene, bounds_name, reason="unknown"):
    """
    Checks if an update is needed for a specific bounds hierarchy based on state change.
    If needed and auto-update is on, resets the debounce timer for viewport updates.
    """
    global _updates_pending
    context = bpy.context
    bounds_obj = context.scene.objects.get(bounds_name)
    if not bounds_obj: return # Bounds object might have been deleted

    # Check the auto-update setting ON THE BOUNDS OBJECT
    if not get_bounds_setting(bounds_obj, "sdf_auto_update"):
        return # Auto update disabled for this system

    # Don't re-trigger if an update is already pending/running for this bounds
    if _updates_pending.get(bounds_name, False):
        return

    # Get the current state ONLY if necessary checks pass
    current_state = get_current_sdf_state(context, bounds_obj)

    if has_state_changed(current_state, bounds_name):
        # State has changed, schedule a new debounce timer
        schedule_new_debounce_timer(scene, bounds_name, current_state)
    # else: State hasn't changed, do nothing


def cancel_debounce_timer(bounds_name):
    """Cancels the active debounce timer for a specific bounds object."""
    global _debounce_timers
    timer = _debounce_timers.pop(bounds_name, None) # Get and remove timer reference
    if timer is not None:
        try:
            bpy.app.timers.unregister(timer)
        except (ValueError, Exception):
            # Timer might have already fired or been unregistered elsewhere
            pass

def schedule_new_debounce_timer(scene, bounds_name, trigger_state):
    """ Schedules a new viewport update timer, cancelling any existing one for this bounds. """
    global _debounce_timers, _last_trigger_states
    context = bpy.context
    bounds_obj = context.scene.objects.get(bounds_name)
    if not bounds_obj: return # Bounds deleted

    # Cancel any previous timer for this specific bounds object
    cancel_debounce_timer(bounds_name)

    # Store the state that triggered this timer scheduling attempt
    _last_trigger_states[bounds_name] = trigger_state

    # Get the delay from the bounds object's settings
    delay = get_bounds_setting(bounds_obj, "sdf_realtime_update_delay")

    try:
        # Use a lambda that captures the specific bounds_name
        new_timer = bpy.app.timers.register(
            lambda name=bounds_name: debounce_check_and_run_viewport_update(scene, name),
            first_interval=delay
        )
        _debounce_timers[bounds_name] = new_timer
    except Exception as e:
         # Log error and clean up state if timer registration fails
         print(f"FieldForge ERROR: Failed to register debounce timer for {bounds_name}: {e}")
         _last_trigger_states.pop(bounds_name, None)


def debounce_check_and_run_viewport_update(scene, bounds_name):
    """
    Timer callback for a specific bounds object. Checks throttle and schedules the actual update.
    Returns None to indicate the timer should not repeat automatically.
    """
    global _debounce_timers, _last_trigger_states, _updates_pending, _last_update_finish_times
    context = bpy.context
    bounds_obj = context.scene.objects.get(bounds_name)
    if not bounds_obj: return None # Bounds deleted, timer is now defunct

    # Timer has fired, remove its reference (it won't fire again unless rescheduled)
    _debounce_timers.pop(bounds_name, None)

    # Check if an update was already manually triggered or is running for this bounds
    if _updates_pending.get(bounds_name, False):
        return None # Let the existing pending update run its course

    # Retrieve the state that caused this timer to be scheduled
    state_to_pass_to_update = _last_trigger_states.get(bounds_name)
    if state_to_pass_to_update is None:
        # This could happen if manually cleared or due to some race condition
        return None

    # --- Throttle Check ---
    min_interval = get_bounds_setting(bounds_obj, "sdf_minimum_update_interval")
    last_finish = _last_update_finish_times.get(bounds_name, 0.0)
    time_since_last_update = time.time() - last_finish

    if time_since_last_update >= min_interval:
        # --- Throttle OK: Schedule the actual viewport update ---
        _last_trigger_states.pop(bounds_name, None) # Clear the trigger state, it's being used now
        _updates_pending[bounds_name] = True # Mark this bounds as having an update pending

        try:
            # Use timer with 0 interval to run the update in the next Blender tick
            # Pass the specific bounds_name and the captured state
            bpy.app.timers.register(
                lambda name=bounds_name, state=state_to_pass_to_update: run_sdf_update(scene, name, state, is_viewport_update=True),
                first_interval=0.0
            )
        except Exception as e:
             # Log error and reset pending flag if scheduling the run fails
             print(f"FieldForge ERROR: Failed to register run_sdf_update timer for {bounds_name}: {e}")
             _updates_pending[bounds_name] = False # Allow retries later

    else:
        # --- Throttle Active: Reschedule this check function ---
        remaining_wait = min_interval - time_since_last_update

        # Keep the state in _last_trigger_states[bounds_name] for the next attempt
        # Do NOT set the pending flag yet
        cancel_debounce_timer(bounds_name) # Ensure no duplicate check timers exist
        try:
            # Reschedule this *check* function again after the throttle interval
            # Use a lambda that captures the specific bounds_name
            new_timer = bpy.app.timers.register(
                lambda name=bounds_name: debounce_check_and_run_viewport_update(scene, name),
                first_interval=remaining_wait
            )
            _debounce_timers[bounds_name] = new_timer # Store the new timer reference
        except Exception as e:
            # Log error and clear state if rescheduling fails, effectively stopping updates for now
            print(f"FieldForge ERROR: Failed to reschedule throttle check for {bounds_name}: {e}")
            _last_trigger_states.pop(bounds_name, None)

    return None # Essential: Prevents the timer from repeating automatically

def run_sdf_update(scene, bounds_name, trigger_state, is_viewport_update=False):
    """
    Performs the core SDF generation and mesh update for a specific bounds hierarchy.
    Reads settings and state from the provided `trigger_state`.
    """
    global _updates_pending, _last_update_finish_times, _sdf_update_caches

    context = bpy.context
    bounds_obj = context.scene.objects.get(bounds_name)

    # --- Pre-computation Checks ---
    if trigger_state is None:
        print(f"FieldForge ERROR: run_sdf_update called with None state for {bounds_name}!")
        _updates_pending[bounds_name] = False; return # Cannot proceed
    if not bounds_obj:
        print(f"FieldForge ERROR: run_sdf_update called for non-existent bounds '{bounds_name}'!")
        _updates_pending[bounds_name] = False; return # Bounds object vanished
    if not libfive_available:
        _updates_pending[bounds_name] = False; return # Libfive became unavailable?

    update_type = "VIEWPORT" if is_viewport_update else "FINAL"

    # --- Main Update Logic ---
    mesh_update_successful = False
    mesh_generation_error = False
    result_obj = None # Define early for use in finally block

    try:
        # Get settings and state info directly from the trigger_state dictionary
        sdf_settings_state = trigger_state.get('scene_settings')
        bounds_matrix = trigger_state.get('bounds_matrix')
        result_name = bounds_obj.get(SDF_RESULT_OBJ_NAME_PROP) # Read prop from current bounds obj

        # Validate necessary state components
        if not sdf_settings_state: raise ValueError("SDF settings missing from trigger state")
        if not bounds_matrix: raise ValueError("Bounds matrix missing from trigger state")
        if not result_name: raise ValueError(f"Result object name property missing from bounds {bounds_name}")

        # 1. Process Hierarchy to get the combined libfive shape
        # Pass the settings dict from the trigger_state
        final_combined_shape = process_sdf_hierarchy(bounds_obj, sdf_settings_state)

        if final_combined_shape is None:
            # Error occurred during hierarchy processing (logged inside function)
            raise ValueError("SDF hierarchy processing failed to return a shape.")

        # 2. Define Meshing Region based on the Bounds object's state at trigger time
        # Use the bounds_matrix from the trigger_state for consistency
        b_loc = bounds_matrix.translation
        # Use average scale from matrix; assumes uniform scaling is intended for bounds visual
        b_sca_vec = bounds_matrix.to_scale()
        avg_scale = max(1e-6, (abs(b_sca_vec.x) + abs(b_sca_vec.y) + abs(b_sca_vec.z)) / 3.0)
        b_half_extent = avg_scale # Use full scale as extent from center? Or half? Half seems right.
        xyz_min = [b_loc[i] - b_half_extent for i in range(3)]
        xyz_max = [b_loc[i] + b_half_extent for i in range(3)]

        # 3. Select Resolution based on update type and settings from trigger_state
        resolution = 0
        if is_viewport_update:
            resolution = sdf_settings_state.get("sdf_viewport_resolution", 10)
        else:
            resolution = sdf_settings_state.get("sdf_final_resolution", 30)
        if resolution < 3: resolution = 3 # Ensure minimum resolution

        # 4. Generate Mesh using libfive
        mesh_data = None
        try:
            # Optimization can sometimes help, but might also fail
            # mesh_shape_opt = final_combined_shape.optimized()
            # mesh_data = mesh_shape_opt.get_mesh(xyz_min=xyz_min, xyz_max=xyz_max, resolution=resolution)
            mesh_data = final_combined_shape.get_mesh(xyz_min=xyz_min, xyz_max=xyz_max, resolution=resolution)
        except Exception as e:
            print(f"FieldForge Error: libfive mesh generation failed for {bounds_name}: {e}")
            mesh_generation_error = True
            # Allow execution to proceed to finally block for cleanup

        # 5. Find or Create Result Object
        result_obj = find_result_object(context, result_name)
        if not result_obj:
            if get_bounds_setting(bounds_obj, "sdf_create_result_object"): # Check setting on current bounds
                try:
                    # Create new mesh data and object
                    mesh_data_new = bpy.data.meshes.new(name=result_name + "_Mesh") # Unique mesh data name
                    result_obj = bpy.data.objects.new(result_name, mesh_data_new)
                    context.collection.objects.link(result_obj)
                    # Reset transform and make unselectable by default
                    result_obj.matrix_world = Matrix.Identity(4)
                    result_obj.hide_select = True
                except Exception as e:
                    # If creation fails, we cannot proceed with mesh update
                    raise ValueError(f"Failed to create result object {result_name}: {e}") from e
            else:
                # Result object doesn't exist and creation is disabled
                raise ValueError(f"Result object '{result_name}' not found, and auto-creation is disabled for {bounds_name}.")

        # Ensure the target object is actually a mesh
        if result_obj.type != 'MESH':
            raise TypeError(f"Target object '{result_name}' for SDF result is not a Mesh (type: {result_obj.type}).")

        # 6. Update Mesh Data (only if mesh generation succeeded)
        if not mesh_generation_error:
            mesh = result_obj.data
            if not mesh_data or not mesh_data[0]: # Handle empty mesh data from libfive
                if mesh.vertices: # Clear existing geometry if needed
                    mesh.clear_geometry()
                    mesh.update()
                mesh_update_successful = True # Considered success (empty result)
            else: # Valid mesh data received
                if mesh.vertices: mesh.clear_geometry() # Clear previous geometry first
                try:
                    # Assign new geometry
                    mesh.from_pydata(mesh_data[0], [], mesh_data[1]) # Vertices, Edges (empty), Faces
                    mesh.update() # Recalculate normals and bounding box
                    mesh_update_successful = True
                except Exception as e:
                    print(f"FieldForge ERROR: Applying mesh data to '{result_name}' failed: {e}")
                    if mesh.vertices: mesh.clear_geometry() # Attempt to clear partially loaded data
                    mesh_update_successful = False # Mark as failed

    except Exception as e:
         # Catch errors during state validation, hierarchy processing, object finding/creation, etc.
         print(f"FieldForge ERROR during {update_type} update for {bounds_name}: {e}")
         mesh_generation_error = True # Mark as failed if any error occurred before/during mesh update
         mesh_update_successful = False
         # Attempt to clear result mesh geometry if an object was found/created
         try:
             if result_obj and result_obj.type == 'MESH' and result_obj.data.vertices:
                 result_obj.data.clear_geometry()
                 result_obj.data.update()
         except Exception: pass # Ignore errors during cleanup

    # --- Final Steps (Cache, Time, Flags) ---
    finally:
        # Update cache ONLY if the entire process including mesh update was successful
        if not mesh_generation_error and mesh_update_successful:
            update_sdf_cache(trigger_state, bounds_name)
        # else: Error already printed, cache remains unchanged

        # Record the finish time for this bounds object's update attempt (success or fail)
        _last_update_finish_times[bounds_name] = time.time()

        # Reset the pending flag for this specific bounds object
        _updates_pending[bounds_name] = False


# --- Scene Update Handler (Monitors Changes) ---
@persistent
def ff_depsgraph_handler(scene, depsgraph):
    """ Blender dependency graph handler, called after updates. """
    if not libfive_available: return

    updated_bounds_names = set() # Track which Bounds hierarchies are affected

    # Check if depsgraph exists and has updates (can be None during file load)
    if depsgraph is None or not hasattr(depsgraph, 'updates'):
        return

    for update in depsgraph.updates:
        id_data = update.id
        target_obj = None

        # Check if the updated ID is an Object
        if isinstance(id_data, bpy.types.Object):
            target_obj = id_data
        # Could also check for material, scene, etc. updates if needed

        if target_obj:
            # Find the root Bounds object for the updated object
            root_bounds = find_parent_bounds(target_obj)
            if root_bounds:
                # Check if the update type is relevant (transform, geometry, custom props?)
                # Custom properties don't trigger depsgraph updates directly.
                # We rely on transform/geometry changes, or manual triggers/UI changes.
                if (update.is_updated_transform or update.is_updated_geometry):
                    updated_bounds_names.add(root_bounds.name)
                # If the updated object *is* the bounds object, check its transform too
                elif target_obj == root_bounds and update.is_updated_transform:
                     updated_bounds_names.add(root_bounds.name)


    # Trigger the check function for each affected bounds hierarchy
    for bounds_name in updated_bounds_names:
        # Use a short timer delay to potentially coalesce multiple triggers per frame?
        # Or call directly? Direct call might be simpler.
        # Let's stick to direct call for now, debounce handles coalescing later.
        check_and_trigger_update(scene, bounds_name, "depsgraph")


# --- Operators ---

class OBJECT_OT_add_sdf_bounds(Operator):
    """Adds a new SDF Bounds controller Empty and prepares its result mesh setup"""
    bl_idname = "object.add_sdf_bounds"
    bl_label = "Add SDF Bounds Controller"
    bl_options = {'REGISTER', 'UNDO'}

    location: FloatVectorProperty(name="Location", default=(0.0, 0.0, 0.0), subtype='TRANSLATION')
    bounds_name_prefix: StringProperty(name="Name Prefix", default="SDF_System")

    def make_unique_name(self, context, base_name):
        """ Generates a unique object name based on the base name. """
        if base_name not in context.scene.objects:
            return base_name
        i = 1
        while f"{base_name}.{i:03d}" in context.scene.objects:
            i += 1
        return f"{base_name}.{i:03d}"

    def execute(self, context):
        # Create Bounds Empty (Cube visual)
        bpy.ops.object.empty_add(type='CUBE', radius=1.0, location=self.location)
        bounds_obj = context.active_object
        if not bounds_obj: return {'CANCELLED'}

        # Generate unique name for the Bounds object itself
        unique_bounds_name = self.make_unique_name(context, self.bounds_name_prefix + "_Bounds")
        bounds_obj.name = unique_bounds_name

        # Initial setup for the Bounds object
        bounds_obj.scale = (2.0, 2.0, 2.0) # Initial visual size
        bounds_obj.empty_display_size = 1.0 # Make the cube visual match the scale
        bounds_obj.color = (0.2, 0.8, 1.0, 1.0) # Distinctive color
        bounds_obj.hide_render = True # Bounds controller doesn't need to render

        # Set the marker property to identify this as a Bounds controller
        bounds_obj[SDF_BOUNDS_MARKER] = True

        # Determine a unique name for the associated Result object
        result_name_base = self.bounds_name_prefix + "_Result"
        final_result_name = self.make_unique_name(context, result_name_base)

        # Store the intended Result Object Name as a custom property on the Bounds object
        bounds_obj[SDF_RESULT_OBJ_NAME_PROP] = final_result_name

        # Store Default Settings as custom properties on the Bounds object
        for key, value in DEFAULT_SETTINGS.items():
            bounds_obj[key] = value

        self.report({'INFO'}, f"Added SDF Bounds: {bounds_obj.name}")

        # Trigger an initial update check for the newly added bounds system
        # Use a timer to ensure it runs after the object is fully integrated
        bpy.app.timers.register(
            lambda name=bounds_obj.name: check_and_trigger_update(context.scene, name, "add_bounds"),
            first_interval=0.01 # Short delay
        )

        # Select only the new Bounds object
        context.view_layer.objects.active = bounds_obj
        for obj in context.selected_objects: obj.select_set(False)
        bounds_obj.select_set(True)

        return {'FINISHED'}


class AddSdfSourceBase(Operator):
    """Base class for adding various SDF source type Empties"""
    bl_options = {'REGISTER', 'UNDO'}

    # Properties common to all source types
    initial_child_blend: FloatProperty(
        name="Child Blend Factor",
        description="Initial blend factor for children parented TO this new object",
        default=0.0, min=0.0, max=5.0, subtype='FACTOR'
    )
    is_negative: BoolProperty(
        name="Negative (Subtractive)",
        description="Make this shape subtract from its parent/siblings",
        default=False
    )

    @classmethod
    def poll(cls, context):
        # Allow adding if libfive is available and the active object can be part of an SDF hierarchy
        # (i.e., it is a bounds object or has a bounds object as an ancestor)
        return libfive_available and context.active_object is not None and find_parent_bounds(context.active_object) is not None

    def invoke(self, context, event):
         # Set initial location to the 3D cursor
         # World location is fine, parenting will handle relative position
         # self.location = context.scene.cursor.location
         # Or maybe better to place relative to parent? Let's use cursor for now.
         wm = context.window_manager
         return wm.invoke_props_dialog(self) # Show options dialog

    def make_unique_name(self, context, base_name):
        """ Generates a unique object name based on the base name. """
        if base_name not in context.scene.objects:
            return base_name
        i = 1
        while f"{base_name}.{i:03d}" in context.scene.objects:
            i += 1
        return f"{base_name}.{i:03d}"

    def add_sdf_empty(self, context, sdf_type, display_type, name_prefix, props_to_set=None):
        """ Helper method to create and configure the SDF source Empty """
        target_parent = context.active_object
        parent_bounds = find_parent_bounds(target_parent)

        if not parent_bounds:
            self.report({'ERROR'}, "Active object is not part of an SDF Bounds hierarchy.")
            return {'CANCELLED'}

        # Create the new Empty at the cursor location
        bpy.ops.object.empty_add(type=display_type, radius=0.5, location=context.scene.cursor.location, scale=(1.0, 1.0, 1.0))
        obj = context.active_object
        if not obj: return {'CANCELLED'}

        # Generate unique name
        unique_name = self.make_unique_name(context, name_prefix)
        obj.name = unique_name

        # Parent the new Empty to the currently active object
        obj.parent = target_parent
        # Set the inverse parent matrix to maintain world position at creation time
        obj.matrix_parent_inverse = target_parent.matrix_world.inverted()

        # Assign standard SDF properties
        obj[SDF_PROPERTY_MARKER] = True # Mark as an SDF object
        obj["sdf_type"] = sdf_type
        obj["sdf_child_blend_factor"] = self.initial_child_blend # Blend factor for ITS children
        obj["sdf_is_negative"] = self.is_negative # Subtractive flag

        # Assign any type-specific properties passed in
        if props_to_set:
            for key, value in props_to_set.items():
                obj[key] = value

        # Set color based on negative flag for visual distinction
        if self.is_negative:
            obj.color = (1.0, 0.3, 0.3, 1.0) # Reddish tint for negative
        else:
            # Default color (can be customized further per type if desired)
            obj.color = (0.5, 0.5, 0.5, 1.0) # Neutral grey

        # Set initial visibility based on the PARENT BOUNDS setting
        show = get_bounds_setting(parent_bounds, "sdf_show_source_empties")
        obj.hide_viewport = not show
        obj.hide_render = not show

        # Select the newly created object
        context.view_layer.objects.active = obj
        for sel_obj in context.selected_objects: sel_obj.select_set(False)
        obj.select_set(True)

        # Report success
        report_msg = f"Added SDF Source: {obj.name} ({sdf_type}) under {target_parent.name}"
        if self.is_negative: report_msg += " [Negative]"
        self.report({'INFO'}, report_msg)

        # Trigger an update check for the PARENT BOUNDS hierarchy this object was added to
        check_and_trigger_update(context.scene, parent_bounds.name, f"add_{sdf_type}_source")
        return {'FINISHED'}


# --- Concrete Operator Classes for Adding Each Source Type ---

class OBJECT_OT_add_sdf_cube_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Cube"""
    bl_idname = "object.add_sdf_cube_source"
    bl_label = "SDF Cube Source"

    def execute(self, context):
        return self.add_sdf_empty( context, "cube", 'CUBE', "FF_Cube" )

class OBJECT_OT_add_sdf_sphere_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Sphere"""
    bl_idname = "object.add_sdf_sphere_source"
    bl_label = "SDF Sphere Source"

    def execute(self, context):
         return self.add_sdf_empty( context, "sphere", 'SPHERE', "FF_Sphere" )

class OBJECT_OT_add_sdf_cylinder_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Cylinder"""
    bl_idname = "object.add_sdf_cylinder_source"
    bl_label = "SDF Cylinder Source"

    def execute(self, context):
         return self.add_sdf_empty( context, "cylinder", 'PLAIN_AXES', "FF_Cylinder" ) # Axes are clearer for orientation

class OBJECT_OT_add_sdf_cone_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Cone"""
    bl_idname = "object.add_sdf_cone_source"
    bl_label = "SDF Cone Source"

    def execute(self, context):
         return self.add_sdf_empty( context, "cone", 'PLAIN_AXES', "FF_Cone" ) # Axes also good for cone orientation

class OBJECT_OT_add_sdf_torus_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Torus"""
    bl_idname = "object.add_sdf_torus_source"
    bl_label = "SDF Torus Source"

    def execute(self, context):
         return self.add_sdf_empty( context, "torus", 'PLAIN_AXES', "FF_Torus" ) # Use CIRCLE? Axes might be okay.

class OBJECT_OT_add_sdf_rounded_box_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Rounded Box"""
    bl_idname = "object.add_sdf_rounded_box_source"
    bl_label = "SDF Rounded Box Source"

    # Add property specific to the rounded box type
    initial_round_radius: FloatProperty(
        name="Rounding Radius",
        description="Initial corner rounding radius (applied before scaling)",
        default=0.1, min=0.0, max=1.0, # Max relative to unit cube size
        subtype='FACTOR' # Or 'DISTANCE'? Factor seems more intuitive pre-scale.
    )

    def execute(self, context):
         # Pass the initial radius to be stored as a custom property
         props = {"sdf_round_radius": self.initial_round_radius}
         return self.add_sdf_empty( context, "rounded_box", 'CUBE', "FF_RoundedBox", props_to_set=props )


class OBJECT_OT_sdf_manual_update(Operator):
    """Manually triggers a high-resolution FINAL update for the ACTIVE SDF Bounds hierarchy."""
    bl_idname = "object.sdf_manual_update"
    bl_label = "Update Final SDF Now"
    bl_options = {'REGISTER'} # No undo needed for triggering an update

    @classmethod
    def poll(cls, context):
        # Enable only if libfive available and the active object IS an SDF Bounds object
        obj = context.active_object
        return libfive_available and obj and obj.get(SDF_BOUNDS_MARKER, False)

    def execute(self, context):
        bounds_obj = context.active_object # Relies on poll ensuring this is a Bounds object
        bounds_name = bounds_obj.name
        print(f"FieldForge: Manual final update triggered for {bounds_name}.")
        global _updates_pending, _last_trigger_states # Access per-bounds dicts

        # Cancel any pending debounce timer specifically for this bounds
        cancel_debounce_timer(bounds_name)
        # Clear any stored trigger state for this bounds, as we're overriding with a manual trigger
        _last_trigger_states.pop(bounds_name, None)

        # Check if an update (viewport or final) is already pending/running for this bounds
        if _updates_pending.get(bounds_name, False):
             self.report({'WARNING'}, f"Update already in progress for {bounds_name}. Manual trigger ignored.")
             return {'CANCELLED'}

        # Get the current state specifically for this bounds to ensure the manual update uses latest info
        current_state_for_manual_update = get_current_sdf_state(context, bounds_obj)
        if not current_state_for_manual_update:
             self.report({'ERROR'}, f"Failed to get current state for manual update of {bounds_name}.")
             return {'CANCELLED'}

        # Mark this specific bounds as pending an update
        _updates_pending[bounds_name] = True
        try:
            # Schedule the FINAL update (is_viewport_update=False) using a timer for the next tick
            # Pass the specific bounds_name and the freshly gathered state
            bpy.app.timers.register(
                lambda name=bounds_name, state=current_state_for_manual_update: run_sdf_update(context.scene, name, state, is_viewport_update=False),
                first_interval=0.0
            )
        except Exception as e:
             # Log error and reset pending flag if scheduling fails
             print(f"FieldForge ERROR: Failed to register FINAL update timer for {bounds_name}: {e}")
             _updates_pending[bounds_name] = False
             self.report({'ERROR'}, f"Failed to schedule final update for {bounds_name}. See console.")
             return {'CANCELLED'}

        self.report({'INFO'}, f"Scheduled final update for {bounds_name}.")
        return {'FINISHED'}


# --- UI Panels ---

class OBJECT_PT_sdf_bounds_settings(Panel):
    """Panel in Object Properties for the selected SDF Bounds object's settings"""
    bl_label = "FieldForge Bounds Settings"
    bl_idname = "OBJECT_PT_sdf_bounds_settings"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object" # Show in Object properties tab

    @classmethod
    def poll(cls, context):
        # Show only if libfive available AND the active object IS the Bounds controller
        obj = context.object
        return libfive_available and obj and obj.get(SDF_BOUNDS_MARKER, False)

    def draw_header(self, context):
        self.layout.label(text="", icon='MOD_BUILD') # Use a relevant icon

    def draw(self, context):
        layout = self.layout
        obj = context.object # The active object IS the Bounds object (due to poll)

        # Access custom properties using dictionary syntax obj["prop_name"]
        # This automatically triggers updates if the property has an 'update' callback (though ours don't directly)
        # and ensures the values are read from/written to the object itself.

        col = layout.column(align=True)

        # Resolution Box
        box_res = col.box()
        box_res.label(text="Resolution:")
        row_res = box_res.row(align=True)
        row_res.prop(obj, '["sdf_viewport_resolution"]', text="Viewport")
        row_res.prop(obj, '["sdf_final_resolution"]', text="Final")

        # Timing Box
        box_time = col.box()
        box_time.label(text="Update Timing:")
        row_time1 = box_time.row(align=True)
        row_time1.prop(obj, '["sdf_realtime_update_delay"]', text="Inactive Delay")
        row_time2 = box_time.row(align=True)
        row_time2.prop(obj, '["sdf_minimum_update_interval"]', text="Min Interval")

        # Blending Box (Global for children of this bounds)
        box_blend = col.box()
        box_blend.label(text="Root Child Blending:")
        row_blend = box_blend.row(align=True)
        row_blend.prop(obj, '["sdf_global_blend_factor"]', text="Blend Factor")

        col.separator()

        # Update Controls Box
        box_upd = col.box()
        box_upd.label(text="Update & Display:")
        row_upd1 = box_upd.row(align=True)
        row_upd1.prop(obj, '["sdf_auto_update"]', text="Auto Viewport Update", toggle=True)
        # Manual Update Button - Always enabled if panel is showing
        row_upd1.operator(OBJECT_OT_sdf_manual_update.bl_idname, text="Update Final", icon='FILE_REFRESH')

        row_upd2 = box_upd.row(align=True)
        row_upd2.prop(obj, '["sdf_show_source_empties"]', text="Show Source Empties")
        row_upd3 = box_upd.row(align=True)
        row_upd3.prop(obj, '["sdf_create_result_object"]', text="Create Result If Missing")

        col.separator()

        # Result Object Box
        box_res_obj = col.box()
        box_res_obj.label(text="Result Object:")
        row_res_obj1 = box_res_obj.row(align=True)
        row_res_obj1.prop(obj, '["sdf_result_object_name"]', text="Name") # Allow editing? Maybe read-only better?
        row_res_obj1.enabled = False # Make name read-only for now to avoid sync issues
        row_res_obj2 = box_res_obj.row(align=True)
        # Button to select the result object
        op = row_res_obj2.operator("object.select_pattern", text="Select Result Object", icon='VIEWZOOM')
        op.pattern = obj.get(SDF_RESULT_OBJ_NAME_PROP, "") # Get name for the operator pattern
        # Disable button if the name property is empty
        row_res_obj2.enabled = obj.get(SDF_RESULT_OBJ_NAME_PROP, "") != ""


class OBJECT_PT_sdf_source_info(Panel):
    """Panel in Object Properties for selected SDF Source Empties"""
    bl_label = "FieldForge Source Properties"
    bl_idname = "OBJECT_PT_sdf_source_info"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object" # Show in Object properties tab

    @classmethod
    def poll(cls, context):
        # Show only if libfive available AND the active object is an SDF source Empty
        obj = context.object
        return libfive_available and obj and is_sdf_source(obj) # is_sdf_source checks type and marker

    def draw_header(self, context):
        self.layout.label(text="", icon='OBJECT_DATA') # Use geometry/data icon

    def draw(self, context):
        layout = self.layout
        obj = context.object # Active object is an SDF source (due to poll)
        sdf_type = obj.get("sdf_type", "Unknown")

        col = layout.column()

        # Basic Info
        col.label(text=f"SDF Type: {sdf_type.capitalize()}")
        col.prop(obj, '["sdf_is_negative"]', text="Negative (Subtractive)", toggle=True, icon='REMOVE')
        col.separator()

        # --- Type-Specific Properties ---
        if sdf_type == "rounded_box":
            box_params = col.box()
            box_params.label(text="Shape Parameters:")
            box_params.prop(obj, '["sdf_round_radius"]', text="Rounding Radius")
            col.separator()
        # Add sections for other types if they get specific parameters

        # --- Blending Settings for Children of THIS object ---
        box_child_blend = col.box()
        box_child_blend.label(text="Child Object Blending:")
        sub_col = box_child_blend.column(align=True)
        # Provide the blend factor property for ITS children
        sub_col.prop(obj, '["sdf_child_blend_factor"]', text="Factor")
        sub_col.label(text="(Smoothness for objects parented directly to this one)")

        col.separator()
        # Info Text
        col.label(text="Transform (Location, Rotation, Scale) controls shape placement.")
        if obj.parent:
            row_parent = col.row()
            row_parent.label(text="Parent:")
            # Show parent field, but maybe disable direct editing from here?
            row_parent.prop(obj, "parent", text="")
            row_parent.enabled = False # Prevent reparenting from this panel


# --- Menu Definition ---

class VIEW3D_MT_add_sdf(Menu):
    """Add menu for FieldForge SDF objects"""
    bl_idname = "VIEW3D_MT_add_sdf"
    bl_label = "Field Forge SDF"

    def draw(self, context):
        layout = self.layout

        # Check if libfive is available first
        if not libfive_available:
            layout.label(text="libfive library not found!", icon='ERROR')
            layout.label(text="Please check console for details.")
            return

        # --- Bounds Controller ---
        layout.operator(OBJECT_OT_add_sdf_bounds.bl_idname, text="Add Bounds Controller", icon='MOD_BUILD')
        layout.separator()

        # --- Source Shapes ---
        # Enable adding sources only if the active object can be a parent within an SDF hierarchy
        active_obj = context.active_object
        can_add_source = active_obj is not None and find_parent_bounds(active_obj) is not None

        col = layout.column()
        col.enabled = can_add_source # Enable/disable the whole column

        col.label(text="Add Source Shape (Child of Active):")
        # List all available source shape operators
        col.operator(OBJECT_OT_add_sdf_cube_source.bl_idname, text="Cube", icon='MESH_CUBE')
        col.operator(OBJECT_OT_add_sdf_sphere_source.bl_idname, text="Sphere", icon='MESH_UVSPHERE')
        col.operator(OBJECT_OT_add_sdf_cylinder_source.bl_idname, text="Cylinder", icon='MESH_CYLINDER')
        col.operator(OBJECT_OT_add_sdf_cone_source.bl_idname, text="Cone", icon='MESH_CONE')
        col.operator(OBJECT_OT_add_sdf_torus_source.bl_idname, text="Torus", icon='MESH_TORUS')
        col.operator(OBJECT_OT_add_sdf_rounded_box_source.bl_idname, text="Rounded Box", icon='MOD_BEVEL')
        # Add other source types here when implemented

        # Add informational text if adding sources is disabled
        if not can_add_source:
             layout.separator()
             layout.label(text="Select Bounds or SDF Source", icon='INFO')
             layout.label(text="to add new child shapes.")


def menu_func(self, context):
    # Adds the Field Forge menu to the main Add > Mesh menu
    self.layout.menu(VIEW3D_MT_add_sdf.bl_idname, icon='MOD_OPACITY')

# --- Initial Update Check on Load/Register ---
def initial_update_check_all():
    """ Schedules an initial state check for all existing Bounds objects. """
    context = bpy.context
    if not context or not context.scene: return None # Scene might not be ready

    if not libfive_available: return None # Don't run checks if libfive failed

    print("FieldForge: Running initial update check for existing bounds...")
    count = 0
    for bounds_obj in get_all_bounds_objects(context):
        # Use a short, staggered timer for each bounds to avoid overwhelming startup
        # and allow Blender UI to remain responsive.
        try:
            bpy.app.timers.register(
                 lambda name=bounds_obj.name: check_and_trigger_update(context.scene, name, "initial_check"),
                 first_interval=0.1 + count * 0.05 # Stagger checks slightly
            )
            count += 1
        except Exception as e:
            print(f"FieldForge ERROR: Failed to schedule initial check for {bounds_obj.name}: {e}")
    if count > 0: print(f"FieldForge: Scheduled initial checks for {count} bounds systems.")
    return None # Timer function should return None


# --- Registration ---

classes = (
    # Operators
    OBJECT_OT_add_sdf_bounds,
    OBJECT_OT_add_sdf_cube_source,
    OBJECT_OT_add_sdf_sphere_source,
    OBJECT_OT_add_sdf_cylinder_source,
    OBJECT_OT_add_sdf_cone_source,
    OBJECT_OT_add_sdf_torus_source,
    OBJECT_OT_add_sdf_rounded_box_source,
    OBJECT_OT_sdf_manual_update,
    # Panels
    OBJECT_PT_sdf_bounds_settings,
    OBJECT_PT_sdf_source_info,
    # Menus
    VIEW3D_MT_add_sdf,
)

# Store handler reference for safe removal
_handler_ref = None

def register():
    """Registers all addon classes, handlers, and menu items."""
    global _handler_ref
    # Clear global state dictionaries on registration to ensure clean start
    global _debounce_timers, _last_trigger_states, _updates_pending, _last_update_finish_times, _sdf_update_caches
    _debounce_timers.clear()
    _last_trigger_states.clear()
    _updates_pending.clear()
    _last_update_finish_times.clear()
    _sdf_update_caches.clear()

    # Ensure libfive path is added (might be redundant if run via __main__, but safe)
    if libfive_python_dir not in sys.path: sys.path.append(libfive_python_dir)

    # Register classes
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass # Class already registered, ignore
        except Exception as e:
            print(f"FieldForge: Failed to register class {cls.__name__}: {e}")

    # Add menu item
    try:
        bpy.types.VIEW3D_MT_add.append(menu_func)
    except Exception as e:
        print(f"FieldForge: Could not add menu item: {e}")

    # Register depsgraph handler only if libfive is available
    if libfive_available:
        handler_list = bpy.app.handlers.depsgraph_update_post
        # Prevent duplicate handlers
        if ff_depsgraph_handler not in handler_list:
            handler_list.append(ff_depsgraph_handler)
        # Store reference for unregistration
        _handler_ref = ff_depsgraph_handler

        # Trigger initial check for existing bounds objects after registration is complete
        bpy.app.timers.register(initial_update_check_all, first_interval=0.5) # Short delay after startup

    print(f"FieldForge: Registered. (libfive available: {libfive_available})")


def unregister():
    """Unregisters all addon classes, handlers, and menu items."""
    global _handler_ref

    # Unregister depsgraph handler
    if _handler_ref:
        handler_list = bpy.app.handlers.depsgraph_update_post
        if _handler_ref in handler_list:
            try:
                handler_list.remove(_handler_ref)
            except ValueError:
                pass # Handler already removed
        _handler_ref = None

    # Remove menu item
    try:
        bpy.types.VIEW3D_MT_add.remove(menu_func)
    except Exception:
        pass # Menu item might already be removed

    # Cancel all active timers managed by the addon
    global _debounce_timers
    for bounds_name in list(_debounce_timers.keys()): # Iterate over a copy of keys
         cancel_debounce_timer(bounds_name)
    _debounce_timers.clear() # Clear the dictionary itself

    # Clear other global state dictionaries
    _last_trigger_states.clear()
    _updates_pending.clear()
    _last_update_finish_times.clear()
    _sdf_update_caches.clear()

    # Unregister classes in reverse order
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass # Class might already be unregistered or Blender is shutting down
        except Exception as e:
            print(f"FieldForge: Failed to unregister class {cls.__name__}: {e}")

    print("FieldForge: Unregistered.")


# --- Main Execution Block (for direct script execution or reload) ---
if __name__ == "__main__":
    # Standard Blender script reload pattern: unregister first, then register
    try:
        unregister()
    except Exception as e:
        print(f"FieldForge: Error during unregister on reload: {e}")
    finally:
        register()
