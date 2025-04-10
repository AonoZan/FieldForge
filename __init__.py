# -*- coding: utf-8 -*-
"""
FieldForge Addon for Blender: Create and manage dynamic Signed Distance Function
(SDF) shapes using libfive, featuring hierarchical blending and per-system controls.
"""

bl_info = {
    "name": "FieldForge",
    "author": "Your Name & libfive Team",
    "version": (0, 5, 2),
    "blender": (4, 4, 0),
    "location": "View3D > Sidebar (N-Panel) > FieldForge Tab | Add > Mesh > Field Forge SDF",
    "description": "Adds and manages dynamic SDF shapes using libfive with hierarchical blending and custom visuals", # Updated description
    "warning": "Requires compiled libfive libraries.",
    "doc_url": "",
    "category": "Add Mesh",
}

import bpy
import os
import sys
import time
from mathutils import Vector, Matrix
import mathutils # Already imported, but good practice
import math

# --- NEW: GPU Drawing Imports ---
import gpu
from gpu_extras.batch import batch_for_shader
# --- End: GPU Drawing Imports ---


from bpy.types import Operator, Menu, Panel, PropertyGroup
from bpy.props import (
    FloatVectorProperty, FloatProperty, IntProperty, PointerProperty,
    StringProperty, EnumProperty, BoolProperty
)
from bpy.app.handlers import persistent

# --- Start: Existing Libfive Loading and Setup Code ---
# (Keep all the libfive import logic, constants, global state,
# helper functions, state gathering, debounce/throttle logic,
# update functions, scene handler exactly as they were before)
# ... (omitted for brevity - assume it's identical up to Operators) ...
addon_dir = os.path.dirname(os.path.realpath(__file__))
libfive_python_dir = os.path.join(addon_dir) # This seems redundant if addon_dir is already correct

# Ensure the addon directory is in the path for libfive *Python module* discovery
if libfive_python_dir not in sys.path:
    sys.path.append(libfive_python_dir)

libfive_base_dir = os.path.join(addon_dir, 'libfive', 'src')

print(f"FieldForge: Attempting to set LIBFIVE_FRAMEWORK_DIR to: {libfive_base_dir}")

# Check if the directory actually exists before setting the env var
if os.path.isdir(libfive_base_dir):
    # Set the environment variable *before* ffi.py is imported.
    # This tells ffi.py's paths_for() function where to look first.
    os.environ['LIBFIVE_FRAMEWORK_DIR'] = libfive_base_dir
    print(f"FieldForge: Set LIBFIVE_FRAMEWORK_DIR environment variable.")
else:
    print(f"FieldForge: Warning - Calculated libfive base directory does not exist: {libfive_base_dir}")
    print(f"FieldForge: Library loading might still fail if libraries are not found elsewhere.")


libfive_available = False
lf = None
ffi = None
try:
    print("FieldForge: Attempting to import libfive.ffi...")
    import libfive.ffi as ffi
    print("FieldForge: Attempting to import libfive.shape...")
    import libfive.shape # Import shape early if stdlib depends on it
    print("FieldForge: Attempting to import libfive.stdlib...")
    import libfive.stdlib as lf

    if hasattr(lf, 'sphere') and hasattr(ffi.lib, 'libfive_tree_const'):
        libfive_available = True
        print("FieldForge: Successfully imported and verified libfive.") # Keep silent in final
    else:
         print("FieldForge: Libfive imported but core/stdlib function check failed.")
         raise ImportError("Core or stdlib function check failed")

except ImportError as e:
    print(f"FieldForge: Error importing libfive: {e}")
    # Provide guidance on library paths - Adjust paths based on libfive_base_dir
    core_lib_path = os.path.join(libfive_base_dir, "src", f"libfive.{'dll' if sys.platform == 'win32' else 'dylib' if sys.platform == 'darwin' else 'so'}")
    stdlib_lib_path = os.path.join(libfive_base_dir, "stdlib", f"libfive-stdlib.{'dll' if sys.platform == 'win32' else 'dylib' if sys.platform == 'darwin' else 'so'}")
    print(f"FieldForge: Ensure compiled libfive libraries exist, e.g.:")
    print(f"  - Core: {core_lib_path}")
    print(f"  - Stdlib: {stdlib_lib_path}")
    # Check the environment variable again if it failed
    current_env_var = os.environ.get('LIBFIVE_FRAMEWORK_DIR', '<Not Set>')
    print(f"FieldForge: Current LIBFIVE_FRAMEWORK_DIR='{current_env_var}'")
    print(f"FieldForge: Addon requires libfive. Dynamic functionality disabled.")
except Exception as e:
    # Catch potential ctypes loading errors more specifically if possible
    if isinstance(e, OSError) and "cannot open shared object file" in str(e).lower():
         print(f"FieldForge: OSError during libfive import (likely library load failure): {e}")
    else:
        print(f"FieldForge: An unexpected error occurred during libfive import: {type(e).__name__}: {e}")
    # Print traceback for unexpected errors
    import traceback
    traceback.print_exc()
    print(f"FieldForge: Dynamic functionality disabled.")

__all__ = ["libfive_available", "lf", "ffi"]

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
_draw_handle = None             # <<< NEW: Handle for the custom draw callback


# --- Default Settings (Applied to new Bounds objects) ---
DEFAULT_SETTINGS = {
    "sdf_final_resolution": 30,     # Resolution for manual/render updates
    "sdf_viewport_resolution": 5,  # Resolution for automatic viewport previews
    "sdf_realtime_update_delay": 0.5, # Inactivity time before viewport update attempt
    "sdf_minimum_update_interval": 1.0, # Minimum time between end of updates
    "sdf_global_blend_factor": 0.1,   # Default blend factor for direct children of Bounds
    "sdf_auto_update": True,        # Enable/disable automatic viewport updates
    "sdf_show_source_empties": True, # Visibility toggle for source Empties AND custom draws
    "sdf_create_result_object": True, # Auto-create result mesh if missing during update
}


# --- Helper Functions ---
# (Keep existing helper functions: get_all_bounds_objects, find_result_object,
# find_parent_bounds, is_sdf_source, update_empty_visibility, get_bounds_setting,
# compare_matrices, compare_dicts, reconstruct_shape, apply_blender_transform_to_sdf,
# combine_shapes, process_sdf_hierarchy)
# ... (omitted for brevity) ...
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
    # This function now primarily controls the standard Empty visibility,
    # the custom draw handler will check the same setting independently.
    if not libfive_available: return
    context = bpy.context
    processed_objects = set() # Track objects globally to avoid re-processing in nested Bounds

    for bounds_obj in get_all_bounds_objects(context):
        bounds_name = bounds_obj.name
        if bounds_name in processed_objects: continue
        processed_objects.add(bounds_name)

        # Read the visibility setting directly from the Bounds object
        show = get_bounds_setting(bounds_obj, "sdf_show_source_empties") # Use helper

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

                    # Check if object exists before accessing props
                    current_child_obj = scene.objects.get(child_name)
                    if not current_child_obj: continue

                    if is_sdf_source(current_child_obj):
                        try:
                            current_child_obj.hide_viewport = not show
                            current_child_obj.hide_render = not show # Keep render hide consistent
                        except ReferenceError: pass # Object might have been deleted during iteration
                        # Add child to queue only if it still exists
                        q.append(current_child_obj)
                    else:
                         # If child is not a source, still traverse its children
                         q.append(current_child_obj)


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
             # --- Single Scaling Factor for Cone Mesh ---
             # Adjust this factor to change the mesh size relative to the visual guide.
             # 1.0 = mesh matches visual guide size (radius 0.5, height 1.0)
             # 0.5 = mesh is half size (radius 0.25, height 0.5) - PREVIOUS STATE
             # 0.7 = mesh is 70% size, etc.
             CONE_MESH_SCALE_FACTOR = 0.449 # <<< TWEAK THIS VALUE
             # -----------------------------------------

             # Calculate mesh dimensions based on unit size and the factor
             mesh_radius = unit_radius * CONE_MESH_SCALE_FACTOR
             mesh_height = unit_height * CONE_MESH_SCALE_FACTOR

             # Base position remains at Z=0 (object origin)
             base_z = 0.0
             shape = lf.cone_z(mesh_radius, mesh_height, base=(0, 0, base_z))
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
        return lf.emptiness() # Return empty on error

    return shape

def apply_blender_transform_to_sdf(shape, obj_matrix_world_inv):
    """ Applies Blender object's inverted world transform to a libfive shape using remap. """
    if shape is None or obj_matrix_world_inv is None:
        # If shape is emptiness, applying transform doesn't change it
        if isinstance(shape, lf.Shape) and shape == lf.emptiness(): return shape
        return None # Cannot transform None or invalid input

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
        return lf.emptiness() # Return empty on error

    return transformed_shape

def combine_shapes(shape_a, shape_b, blend_factor):
    """ Combines two libfive shapes using union or blend (blend_expt_unit). """
    # Handle cases where one input might be None (error condition) or represent emptiness
    if shape_b is None: return shape_a # If b is invalid, return a
    if shape_a is None: return shape_b # If a is invalid, return b

    try:
        # Ensure blend factor is non-negative
        safe_blend = max(0.0, blend_factor)
        if safe_blend > CACHE_PRECISION:
            # Use blend_expt_unit for smooth blending, normalized factor 0-1 expected visually
            # Clamp factor just in case, though UI should limit it.
            clamped_factor = min(1.0, safe_blend) # Clamp upper bound
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
    if not libfive_available or not obj: return lf.emptiness() # Return empty for invalid input

    # Skip processing if the object is hidden in the viewport (unless it's the Bounds root itself)
    if not obj.visible_get() and not obj.get(SDF_BOUNDS_MARKER, False):
        return lf.emptiness() # Return empty shape for hidden objects

    obj_name = obj.name
    shape_so_far = None
    is_bounds_root = obj.get(SDF_BOUNDS_MARKER, False)

    # 1. Get Base Shape (if the current object is an SDF source)
    if is_sdf_source(obj):
        base_shape = reconstruct_shape(obj) # Returns lf.emptiness() on failure or unknown type
        # Check if reconstruct_shape returned *something* (even emptiness initially)
        if base_shape is not None:
            try:
                # Attempt transformation. apply_blender_transform_to_sdf will handle
                # base_shape == lf.emptiness() or return lf.emptiness() on matrix error.
                obj_matrix_inv = obj.matrix_world.inverted()
                shape_so_far = apply_blender_transform_to_sdf(base_shape, obj_matrix_inv)
            except ValueError: # Matrix inversion failed (e.g., scale is zero)
                print(f"FieldForge Warning: Could not invert matrix for {obj_name}. Skipping object shape.")
                # shape_so_far remains None here
            except Exception as e:
                 print(f"FieldForge Error: Transform application failed for {obj_name}: {e}")
                 # shape_so_far remains None here

    # Initialize with emptiness if no base shape was generated (e.g., it's just a parent/group or shape failed)
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
        if child_shape is not None:
            # Check if child is marked as negative (subtractive)
            is_child_negative = child.get("sdf_is_negative", False)

            if is_child_negative:
                # Apply difference operation (potentially blended)
                try:
                    # Ensure blend factor is non-negative for difference blending
                    safe_blend = max(0.0, interaction_blend_factor)
                    if safe_blend > CACHE_PRECISION:
                         # Clamp upper bound for blend_difference too? Usually 0-1 range makes sense.
                         clamped_blend = min(1.0, safe_blend)
                         shape_so_far = lf.blend_difference(shape_so_far, child_shape, clamped_blend)
                    else:
                        shape_so_far = lf.difference(shape_so_far, child_shape) # Sharp difference

                except Exception as e:
                    print(f"FieldForge Error: Difference operation failed for parent {obj_name}, child {child.name}: {e}")
                    # Continue with the next child if difference fails
                    continue
            else:
                # Apply union or blend operation
                shape_so_far = combine_shapes(shape_so_far, child_shape, interaction_blend_factor)

    return shape_so_far

# --- State Gathering and Caching ---
# (Keep get_current_sdf_state, has_state_changed, update_sdf_cache as they were)
# ... (omitted for brevity) ...
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

            elif actual_child_obj.type == 'EMPTY' and actual_child_obj.children: # Check type for safety
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
# (Keep check_and_trigger_update, cancel_debounce_timer, schedule_new_debounce_timer,
# debounce_check_and_run_viewport_update, run_sdf_update as they were)
# ... (omitted for brevity) ...
def check_and_trigger_update(scene, bounds_name, reason="unknown"):
    """
    Checks if an update is needed for a specific bounds hierarchy based on state change.
    If needed and auto-update is on, resets the debounce timer for viewport updates.
    """
    global _updates_pending
    context = bpy.context
    if not context or not context.scene: return # Context might not be ready (e.g. during startup)
    bounds_obj = context.scene.objects.get(bounds_name)
    if not bounds_obj: return # Bounds object might have been deleted

    # Check the auto-update setting ON THE BOUNDS OBJECT
    if not get_bounds_setting(bounds_obj, "sdf_auto_update"):
        # Also trigger UI redraw if auto-update changed, maybe?
        # bpy.context.window_manager.windows[0].screen.areas[#].tag_redraw() # Complex
        return # Auto update disabled for this system

    # Don't re-trigger if an update is already pending/running for this bounds
    if _updates_pending.get(bounds_name, False):
        return

    # Get the current state ONLY if necessary checks pass
    current_state = get_current_sdf_state(context, bounds_obj)

    if has_state_changed(current_state, bounds_name):
        # State has changed, schedule a new debounce timer
        schedule_new_debounce_timer(scene, bounds_name, current_state)
        # Trigger redraw for custom visuals too, as state impacting SDF likely impacts visuals
        tag_redraw_all_view3d()
    # else: State hasn't changed, do nothing


def cancel_debounce_timer(bounds_name):
    """Cancels the active debounce timer for a specific bounds object."""
    global _debounce_timers
    timer = _debounce_timers.pop(bounds_name, None) # Get and remove timer reference
    if timer is not None:
        try:
            # Check if timer is still registered before trying to unregister
            if bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
        except (ValueError, TypeError, ReferenceError): # Catch potential issues
            # Timer might have already fired or been unregistered elsewhere, or Blender state is unusual
            pass
        except Exception as e:
            print(f"FieldForge WARN: Unexpected error cancelling timer for {bounds_name}: {e}")


def schedule_new_debounce_timer(scene, bounds_name, trigger_state):
    """ Schedules a new viewport update timer, cancelling any existing one for this bounds. """
    global _debounce_timers, _last_trigger_states
    context = bpy.context
    if not context or not context.scene: return # Context might not be ready
    bounds_obj = context.scene.objects.get(bounds_name)
    if not bounds_obj: return # Bounds deleted

    # Cancel any previous timer for this specific bounds object
    cancel_debounce_timer(bounds_name)

    # Store the state that triggered this timer scheduling attempt
    _last_trigger_states[bounds_name] = trigger_state

    # Get the delay from the bounds object's settings
    delay = get_bounds_setting(bounds_obj, "sdf_realtime_update_delay")

    try:
        # Ensure delay is non-negative
        safe_delay = max(0.0, delay)
        # Use a lambda that captures the specific bounds_name
        new_timer = bpy.app.timers.register(
            lambda name=bounds_name: debounce_check_and_run_viewport_update(scene, name),
            first_interval=safe_delay
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
    if not context or not context.scene: return None # Context might not be ready
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
            # Ensure wait time is non-negative
            safe_wait = max(0.0, remaining_wait)
            # Use a lambda that captures the specific bounds_name
            new_timer = bpy.app.timers.register(
                lambda name=bounds_name: debounce_check_and_run_viewport_update(scene, name),
                first_interval=safe_wait
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
    if not context or not context.scene:
        print(f"FieldForge ERROR: Context/Scene not available during run_sdf_update for {bounds_name}.")
        if bounds_name in _updates_pending: _updates_pending[bounds_name] = False; # Try to clear flag
        return # Cannot proceed reliably
    bounds_obj = context.scene.objects.get(bounds_name)

    # --- Pre-computation Checks ---
    if trigger_state is None:
        print(f"FieldForge ERROR: run_sdf_update called with None state for {bounds_name}!")
        if bounds_name in _updates_pending: _updates_pending[bounds_name] = False;
        return # Cannot proceed
    if not bounds_obj:
        print(f"FieldForge ERROR: run_sdf_update called for non-existent bounds '{bounds_name}'!")
        if bounds_name in _updates_pending: _updates_pending[bounds_name] = False;
        return # Bounds object vanished
    if not libfive_available:
        if bounds_name in _updates_pending: _updates_pending[bounds_name] = False;
        return # Libfive became unavailable?

    update_type = "VIEWPORT" if is_viewport_update else "FINAL"
    start_time = time.time()
    # print(f"FieldForge: Starting {update_type} update for {bounds_name}...")


    # --- Main Update Logic ---
    mesh_update_successful = False
    mesh_generation_error = False
    result_obj = None # Define early for use in finally block

    try:
        # Get settings and state info directly from the trigger_state dictionary
        sdf_settings_state = trigger_state.get('scene_settings')
        bounds_matrix = trigger_state.get('bounds_matrix')
        # Read the *current* result object name property from the *current* bounds object.
        # It's less likely to change than transforms, but safer to read current value.
        result_name = bounds_obj.get(SDF_RESULT_OBJ_NAME_PROP)

        # Validate necessary state components
        if not sdf_settings_state: raise ValueError("SDF settings missing from trigger state")
        if not bounds_matrix: raise ValueError("Bounds matrix missing from trigger state")
        if not result_name: raise ValueError(f"Result object name property missing from bounds {bounds_name}")

        # 1. Process Hierarchy to get the combined libfive shape
        # Need the *current* bounds object for hierarchy traversal start point
        final_combined_shape = process_sdf_hierarchy(bounds_obj, sdf_settings_state)

        if final_combined_shape is None: # Should return lf.emptiness() on error now
            final_combined_shape = lf.emptiness()
            print(f"FieldForge WARN: SDF hierarchy processing returned None for {bounds_name}, using empty.")
            # raise ValueError("SDF hierarchy processing failed to return a shape.")


        # 2. Define Meshing Region based on the Bounds object's state at trigger time
        # Use the bounds_matrix from the trigger_state for consistency
        b_loc = bounds_matrix.translation
        # Use average scale from matrix; assumes uniform scaling is intended for bounds visual
        b_sca_vec = bounds_matrix.to_scale()
        # Ensure scale components are positive for average calculation
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
        gen_start_time = time.time()
        # Let get_mesh handle emptiness; it should return None or empty data.
        try:
            mesh_data = final_combined_shape.get_mesh(xyz_min=xyz_min, xyz_max=xyz_max, resolution=resolution)
        except Exception as e:
            # Log error only if the shape wasn't expected to be empty
            # Let's log always for now, but be aware it might log for intentional emptiness.
            print(f"FieldForge Error: libfive mesh generation failed for {bounds_name}: {e}")
            mesh_generation_error = True
        gen_duration = time.time() - gen_start_time
        # print(f"FieldForge: Mesh gen took {gen_duration:.3f}s for {bounds_name} (Res: {resolution})")

        # 5. Find or Create Result Object
        result_obj = find_result_object(context, result_name)
        if not result_obj:
            # Use get_bounds_setting to read the setting from the *current* bounds obj
            if get_bounds_setting(bounds_obj, "sdf_create_result_object"):
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
                # Only raise error if we actually had a shape to mesh
                if final_combined_shape != lf.emptiness():
                    raise ValueError(f"Result object '{result_name}' not found, and auto-creation is disabled for {bounds_name}.")
                else:
                    # No result obj, no shape, nothing to do. Not an error.
                     mesh_update_successful = True # Considered success (empty result expected)


        # 6. Update Mesh Data (only if result object exists and mesh gen didn't error)
        if result_obj and not mesh_generation_error:
            # Ensure the target object is actually a mesh
            if result_obj.type != 'MESH':
                raise TypeError(f"Target object '{result_name}' for SDF result is not a Mesh (type: {result_obj.type}).")

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
         import traceback
         traceback.print_exc() # Print stack trace for better debugging
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
        if bounds_name in _updates_pending: # Check existence before accessing
            _updates_pending[bounds_name] = False

        end_time = time.time()
        # print(f"FieldForge: Finished {update_type} update for {bounds_name} in {end_time - start_time:.3f}s (Success: {mesh_update_successful})")

# --- Scene Update Handler (Monitors Changes) ---
# (Keep ff_depsgraph_handler as it was)
@persistent
def ff_depsgraph_handler(scene, depsgraph):
    """ Blender dependency graph handler, called after updates. """
    if not libfive_available: return

    # Optimization: Exit early if Blender is exiting or context is bad
    if not bpy.context or not bpy.context.window_manager or not bpy.context.window_manager.windows:
         return
    # Optimization: Avoid running during file read or render jobs if possible
    if bpy.app.background: return # Don't run in background mode
    if bpy.context.screen and hasattr(bpy.context.screen, 'is_scrubbing') and bpy.context.screen.is_scrubbing:
        return # Avoid updates while scrubbing timeline


    updated_bounds_names = set() # Track which Bounds hierarchies are affected

    # Check if depsgraph exists and has updates (can be None during file load)
    if depsgraph is None or not hasattr(depsgraph, 'updates'):
        return

    needs_redraw = False # Flag if any relevant update occurred for custom drawing

    for update in depsgraph.updates:
        id_data = update.id
        target_obj = None

        # Check if the updated ID is an Object
        if isinstance(id_data, bpy.types.Object):
            try:
                target_obj = id_data.evaluated_get(depsgraph) if depsgraph else id_data # Get evaluated object
                if not target_obj: # Check if evaluated_get returned None
                    continue
            except ReferenceError: # Object might be gone
                continue


        # Could also check for material, scene, etc. updates if needed

        if target_obj:
            is_source = is_sdf_source(target_obj) # Check if it's one of our source empties

            # Find the root Bounds object for the updated object
            root_bounds = find_parent_bounds(target_obj)

            if root_bounds:
                # Trigger SDF RECOMPUTE if transform/geometry changed
                if update.is_updated_transform or update.is_updated_geometry:
                    updated_bounds_names.add(root_bounds.name)
                    if is_source: needs_redraw = True # Transform change needs redraw
                # If the updated object *is* the bounds object, check its transform too
                elif target_obj == root_bounds and update.is_updated_transform:
                     updated_bounds_names.add(root_bounds.name)
                     needs_redraw = True # Bounds transform change needs redraw for children? No, only SDF recalc.

            # Trigger REDRAW only if a source object's visibility or custom props potentially changed
            # Note: Custom props don't trigger depsgraph, this happens via UI updates or check_and_trigger
            #if is_source and update.is_updated_visible: # Visibility change needs redraw
             #   needs_redraw = True


    # Trigger the check function for each affected bounds hierarchy for SDF RECOMPUTE
    for bounds_name in updated_bounds_names:
        try:
             # Make sure scene object is valid before passing
             current_scene = bpy.context.scene
             if current_scene and current_scene.name:
                 check_and_trigger_update(current_scene, bounds_name, "depsgraph")
             # else: Cannot reliably get scene
        except ReferenceError: pass # Scene or object might be gone
        except Exception as e:
            print(f"FieldForge ERROR: Unexpected error in depsgraph handler triggering update for {bounds_name}: {e}")

    # Trigger redraw if needed for custom visuals
    if needs_redraw:
        tag_redraw_all_view3d()


# --- Operators ---
# (Keep existing operators: OBJECT_OT_add_sdf_bounds, AddSdfSourceBase,
# concrete source adders, OBJECT_OT_sdf_manual_update)
# ... (omitted for brevity) ...
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
            try:
                bounds_obj[key] = value
            except TypeError as e:
                print(f"FieldForge WARN: Could not set default property '{key}' on {bounds_obj.name}: {e}. Value: {value} (Type: {type(value)})")


        self.report({'INFO'}, f"Added SDF Bounds: {bounds_obj.name}")

        # Select only the new Bounds object
        context.view_layer.objects.active = bounds_obj
        for obj in context.selected_objects: obj.select_set(False)
        bounds_obj.select_set(True)

        # Trigger an initial update check for the newly added bounds system
        # Use a timer to ensure it runs after the object is fully integrated and properties are set
        bpy.app.timers.register(
            lambda name=bounds_obj.name: check_and_trigger_update(context.scene, name, "add_bounds"),
            first_interval=0.01 # Short delay
        )
        # Also trigger redraw for potential visuals
        tag_redraw_all_view3d()


        return {'FINISHED'}


class AddSdfSourceBase(Operator):
    """Base class for adding various SDF source type Empties"""
    bl_options = {'REGISTER', 'UNDO'}

    # Properties common to all source types
    initial_child_blend: FloatProperty(
        name="Child Blend Factor",
        description="Initial blend factor for children parented TO this new object",
        default=0.1, min=0.0, max=5.0, subtype='FACTOR' # Allow 0 blend
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
        return libfive_available and context.active_object is not None and (context.active_object.get(SDF_BOUNDS_MARKER, False) or find_parent_bounds(context.active_object) is not None)

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
        # Find the ultimate root bounds for visibility settings
        parent_bounds = find_parent_bounds(target_parent)
        if not parent_bounds:
             # If the active object IS the bounds object, use it directly
            if target_parent.get(SDF_BOUNDS_MARKER, False):
                parent_bounds = target_parent
            else:
                self.report({'ERROR'}, "Active object is not part of an SDF Bounds hierarchy.")
                return {'CANCELLED'}

        # Create the new Empty at the cursor location
        bpy.ops.object.empty_add(type=display_type, radius=0, location=context.scene.cursor.location, scale=(1.0, 1.0, 1.0))
        obj = context.active_object
        if not obj: return {'CANCELLED'}

        # Generate unique name
        unique_name = self.make_unique_name(context, name_prefix)
        obj.name = unique_name

        # Parent the new Empty to the currently active object
        obj.parent = target_parent
        # Set the inverse parent matrix to maintain world position at creation time
        try:
             obj.matrix_parent_inverse = target_parent.matrix_world.inverted()
        except ValueError:
             print(f"FieldForge WARN: Could not invert parent matrix for {target_parent.name}. New object '{obj.name}' might have incorrect initial position relative to parent.")
             obj.matrix_parent_inverse.identity() # Set to identity as fallback


        # Assign standard SDF properties
        obj[SDF_PROPERTY_MARKER] = True # Mark as an SDF object
        obj["sdf_type"] = sdf_type
        obj["sdf_child_blend_factor"] = self.initial_child_blend # Blend factor for ITS children
        obj["sdf_is_negative"] = self.is_negative # Subtractive flag

        # Assign any type-specific properties passed in
        if props_to_set:
            for key, value in props_to_set.items():
                try:
                    obj[key] = value
                except TypeError as e:
                     print(f"FieldForge WARN: Could not set property '{key}' on {obj.name}: {e}. Value: {value} (Type: {type(value)})")


        # Set color based on negative flag for visual distinction IN THE OUTLINER/PROPS
        if self.is_negative:
            obj.color = (1.0, 0.3, 0.3, 1.0) # Reddish tint for negative
        else:
            # Default color (can be customized further per type if desired)
            obj.color = (0.5, 0.5, 0.5, 1.0) # Neutral grey

        # Set initial STANDARD visibility based on the PARENT BOUNDS setting
        show_standard_empty = get_bounds_setting(parent_bounds, "sdf_show_source_empties")
        obj.hide_viewport = not show_standard_empty
        obj.hide_render = not show_standard_empty # Keep render hide consistent

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
        # Also trigger redraw for custom visuals
        tag_redraw_all_view3d()
        return {'FINISHED'}


# --- Concrete Operator Classes for Adding Each Source Type ---

class OBJECT_OT_add_sdf_cube_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Cube"""
    bl_idname = "object.add_sdf_cube_source"
    bl_label = "SDF Cube Source"

    def execute(self, context):
        return self.add_sdf_empty( context, "cube", 'PLAIN_AXES', "FF_Cube" ) # Keep axes for manipulator

class OBJECT_OT_add_sdf_sphere_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Sphere"""
    bl_idname = "object.add_sdf_sphere_source"
    bl_label = "SDF Sphere Source"

    def execute(self, context):
         return self.add_sdf_empty( context, "sphere", 'PLAIN_AXES', "FF_Sphere" ) # Keep axes

class OBJECT_OT_add_sdf_cylinder_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Cylinder"""
    bl_idname = "object.add_sdf_cylinder_source"
    bl_label = "SDF Cylinder Source"

    def execute(self, context):
         return self.add_sdf_empty( context, "cylinder", 'PLAIN_AXES', "FF_Cylinder" ) # Keep axes

class OBJECT_OT_add_sdf_cone_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Cone"""
    bl_idname = "object.add_sdf_cone_source"
    bl_label = "SDF Cone Source"

    def execute(self, context):
         return self.add_sdf_empty( context, "cone", 'PLAIN_AXES', "FF_Cone" ) # Keep axes

class OBJECT_OT_add_sdf_torus_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Torus"""
    bl_idname = "object.add_sdf_torus_source"
    bl_label = "SDF Torus Source"

    def execute(self, context):
         return self.add_sdf_empty( context, "torus", 'PLAIN_AXES', "FF_Torus" ) # Keep axes

class OBJECT_OT_add_sdf_rounded_box_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Rounded Box"""
    bl_idname = "object.add_sdf_rounded_box_source"
    bl_label = "SDF Rounded Box Source"

    # Add property specific to the rounded box type
    initial_round_radius: FloatProperty(
        name="Rounding Radius",
        description="Initial corner rounding radius (applied before scaling)",
        default=0.1, min=0.0, max=1.0, # Max relative to unit cube size
        subtype='DISTANCE' # Use DISTANCE for potentially better interaction with scale later
    )

    def execute(self, context):
         # Pass the initial radius to be stored as a custom property
         props = {"sdf_round_radius": self.initial_round_radius}
         return self.add_sdf_empty( context, "rounded_box", 'PLAIN_AXES', "FF_RoundedBox", props_to_set=props ) # Keep axes


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
# (Keep UI Helper Functions draw_sdf_bounds_settings, draw_sdf_source_info
# and the main VIEW3D_PT_fieldforge_main Panel class as they were)
# ... (omitted for brevity) ...
# --- UI Helper Functions ---

def draw_sdf_bounds_settings(layout, context):
    """ Draws the UI elements for the Bounds object settings. """
    obj = context.object # Assumes the active object IS the Bounds object

    # Access custom properties using dictionary syntax obj["prop_name"]
    # or obj.get("prop_name")

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
    # Update label to clarify it controls both standard empty and custom draw visibility
    row_upd2.prop(obj, '["sdf_show_source_empties"]', text="Show Source Visuals")
    row_upd3 = box_upd.row(align=True)
    row_upd3.prop(obj, '["sdf_create_result_object"]', text="Create Result If Missing")

    col.separator()

    # Result Object Box
    box_res_obj = col.box()
    box_res_obj.label(text="Result Object:")
    result_name = obj.get(SDF_RESULT_OBJ_NAME_PROP, "") # Get current name safely
    row_res_obj1 = box_res_obj.row(align=True)
    row_res_obj1.prop(obj, f'["{SDF_RESULT_OBJ_NAME_PROP}"]', text="Name") # Access property for display
    row_res_obj1.enabled = False # Make name read-only for now to avoid sync issues
    row_res_obj2 = box_res_obj.row(align=True)
    # Button to select the result object
    op = row_res_obj2.operator("object.select_pattern", text="Select Result Object", icon='VIEWZOOM')
    op.pattern = result_name # Pass name to the operator pattern
    op.extend = False # Don't extend selection
    # Disable button if the name property is empty or object not found
    row_res_obj2.enabled = bool(result_name and result_name in context.scene.objects)


def draw_sdf_source_info(layout, context):
    """ Draws the UI elements for the SDF Source object properties. """
    obj = context.object # Assumes the active object is an SDF source
    sdf_type = obj.get("sdf_type", "Unknown")

    col = layout.column()

    # Basic Info
    col.label(text=f"SDF Type: {sdf_type.capitalize()}")
    prop_row = col.row()
    prop_row.prop(obj, '["sdf_is_negative"]', text="Negative (Subtractive)", toggle=True, icon='REMOVE')
    # Add tooltip explaining color change for custom draw
    prop_row.label(text="", icon='INFO')
    prop_row.active = False # Make label non-interactive, purely informational
    if obj.get("sdf_is_negative"):
        prop_row.label(text=" (Draws Red)")
    else:
        prop_row.label(text=" (Draws White)")


    col.separator()

    # --- Type-Specific Properties ---
    if sdf_type == "rounded_box":
        box_params = col.box()
        box_params.label(text="Shape Parameters:")
        box_params.prop(obj, '["sdf_round_radius"]', text="Rounding Radius")
        col.separator()
    # Add sections for other types if they get specific parameters here
    # Example:
    # elif sdf_type == "some_other_type":
    #     box_params = col.box()
    #     box_params.label(text="Shape Parameters:")
    #     box_params.prop(obj, '["some_param"]', text="Some Parameter")
    #     col.separator()

    # --- Blending Settings for Children of THIS object ---
    box_child_blend = col.box()
    box_child_blend.label(text="Child Object Blending:")
    sub_col = box_child_blend.column(align=True)
    # Provide the blend factor property for ITS children
    sub_col.prop(obj, '["sdf_child_blend_factor"]', text="Factor")
    sub_col.label(text="(Smoothness for objects parented directly to this one)")

    col.separator()
    # Info Text
    col.label(text="Transform controls shape placement.")
    if obj.parent:
        row_parent = col.row()
        row_parent.label(text="Parent:")
        # Show parent field, but maybe disable direct editing from here?
        row_parent.prop(obj, "parent", text="")
        row_parent.enabled = False # Prevent reparenting from this panel


# --- Main Viewport Panel ---

class VIEW3D_PT_fieldforge_main(Panel):
    """Main FieldForge Panel in the 3D Viewport Sidebar (N-Panel)"""
    bl_label = "FieldForge Controls" # Panel header label inside the tab
    bl_idname = "VIEW3D_PT_fieldforge_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI' # N-Panel Sidebar region
    bl_category = "FieldForge" # <--- This creates the Tab name

    # No bl_context needed, we check the active object type manually

    # Add an update function to redraw the view when panel properties change
    # This is important for the Show Source Visuals toggle
    def check(self, context):
        # Simple check: redraw if active object changes or libfive availability changes
        # More complex checks could involve custom properties, but can be slow.
        # Rely on operators/handlers to tag redraw for specific prop changes.
        return True

    def draw(self, context):
        layout = self.layout
        obj = context.object # Get the currently active object

        if not libfive_available:
            layout.label(text="libfive library not found!", icon='ERROR')
            layout.separator()
            layout.label(text="Check Blender Console for errors.")
            layout.label(text="Dynamic features disabled.")
            return

        if not obj:
            layout.label(text="Select a FieldForge Bounds", icon='INFO')
            layout.label(text="or Source object.")
            return

        # Check if the active object is a Bounds controller
        if obj.get(SDF_BOUNDS_MARKER, False):
            # Draw the bounds settings using the helper function
            layout.label(text=f"Bounds: {obj.name}", icon='MOD_BUILD')
            layout.separator()
            draw_sdf_bounds_settings(layout, context)

        # Check if the active object is an SDF Source object
        elif is_sdf_source(obj):
            # Draw the source object info using the helper function
            layout.label(text=f"Source: {obj.name}", icon='OBJECT_DATA')
            layout.separator()
            draw_sdf_source_info(layout, context)

        else:
            # Active object is not part of FieldForge system
            layout.label(text="Active object is not", icon='QUESTION')
            layout.label(text="a FieldForge Bounds or Source.")

# --- Menu Definition ---
# (Keep VIEW3D_MT_add_sdf Menu class and menu_func as they were)
# ... (omitted for brevity) ...
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
        can_add_source = active_obj is not None and (active_obj.get(SDF_BOUNDS_MARKER, False) or find_parent_bounds(active_obj) is not None)

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


# --- NEW: Custom Draw Geometry Functions ---
def create_circle_vertices(center, right, up, radius, segments):
    """Generates vertices for a circle in world space."""
    if segments < 3: return []
    vertices = []
    for i in range(segments):
        angle = (i / segments) * 2 * math.pi
        offset = (right * math.cos(angle) + up * math.sin(angle)) * radius
        vertices.append(center + offset)
    return vertices

def create_rectangle_vertices(center, right, up, width, height):
    """Generates vertices for a rectangle in world space."""
    half_w, half_h = width / 2.0, height / 2.0
    tr = (right * half_w) + (up * half_h)
    tl = (-right * half_w) + (up * half_h)
    bl = (-right * half_w) - (up * half_h)
    br = (right * half_w) - (up * half_h)
    # Order for LINE_LOOP: TR -> TL -> BL -> BR -> (implicitly back to TR)
    return [center + tr, center + tl, center + bl, center + br]

def create_rounded_rectangle_vertices(center, right, up, width, height, radius, segments_per_corner):
    """Generates vertices for a rounded rectangle in world space."""
    if segments_per_corner < 1: segments_per_corner = 1
    if radius <= 0.0001: return create_rectangle_vertices(center, right, up, width, height)

    half_w, half_h = width / 2.0, height / 2.0
    radius = min(radius, half_w, half_h) # Clamp radius
    inner_w, inner_h = half_w - radius, half_h - radius

    # Calculate corner centers
    center_tr = center + (right * inner_w) + (up * inner_h)
    center_tl = center + (-right * inner_w) + (up * inner_h)
    center_bl = center + (-right * inner_w) - (up * inner_h)
    center_br = center + (right * inner_w) - (up * inner_h)

    vertices = []
    delta_angle = (math.pi / 2.0) / segments_per_corner

    # Top Right corner (0 to pi/2)
    for i in range(segments_per_corner + 1):
        angle = i * delta_angle
        offset = (right * math.cos(angle) + up * math.sin(angle)) * radius
        vertices.append(center_tr + offset)

    # Top Left corner (pi/2 to pi)
    for i in range(1, segments_per_corner + 1): # Skip first vert (duplicate of last TR)
        angle = (math.pi / 2.0) + (i * delta_angle)
        offset = (right * math.cos(angle) + up * math.sin(angle)) * radius
        vertices.append(center_tl + offset)

    # Bottom Left corner (pi to 3pi/2)
    for i in range(1, segments_per_corner + 1): # Skip first vert
        angle = math.pi + (i * delta_angle)
        offset = (right * math.cos(angle) + up * math.sin(angle)) * radius
        vertices.append(center_bl + offset)

    # Bottom Right corner (3pi/2 to 2pi)
    for i in range(1, segments_per_corner + 1): # Skip first vert
        angle = (3 * math.pi / 2.0) + (i * delta_angle)
        offset = (right * math.cos(angle) + up * math.sin(angle)) * radius
        vertices.append(center_br + offset)

    return vertices

unit_cube_verts = [
    # Bottom face (-Z)
    (-0.5, -0.5, -0.5), (+0.5, -0.5, -0.5), (+0.5, +0.5, -0.5), (-0.5, +0.5, -0.5),
    # Top face (+Z)
    (-0.5, -0.5, +0.5), (+0.5, -0.5, +0.5), (+0.5, +0.5, +0.5), (-0.5, +0.5, +0.5),
]
# Define indices to draw the cube edges using LINE_STRIP or LINES
# Using LINES (12 pairs of indices for 12 edges)
unit_cube_indices = [
    # Bottom face
    (0, 1), (1, 2), (2, 3), (3, 0),
    # Top face
    (4, 5), (5, 6), (6, 7), (7, 4),
    # Connecting edges
    (0, 4), (1, 5), (2, 6), (3, 7)
]

# Convert tuple list to flat list if needed by batch_for_shader with indices later
# flat_cube_indices = [i for pair in unit_cube_indices for i in pair]

# Generate vertices for a unit circle (radius 0.5) in the XY plane
def create_unit_circle_vertices_xy(segments):
    if segments < 3: return []
    vertices = []
    radius = 0.5
    for i in range(segments):
        angle = (i / segments) * 2 * math.pi
        # Z is 0 for XY plane circle
        vertices.append( (math.cos(angle) * radius, math.sin(angle) * radius, 0.0) )
    return vertices

def create_torus_visual_loops(major_radius, minor_radius, main_segments, minor_segments):
    """
    Generates lists of local vertices for the torus visualization.
    Minor loops are now oriented correctly as cross-sections.
    Returns a list containing 5 lists of vertices:
    [main_loop_verts, top_minor_loop_verts, bottom_minor_loop_verts, right_minor_loop_verts, left_minor_loop_verts]
    """
    loops_verts = []

    # 1. Main Loop (Major Radius) in XY plane
    main_loop_verts = []
    if main_segments >= 3:
        for i in range(main_segments): # Generate N points
            angle = (i / main_segments) * 2 * math.pi
            main_loop_verts.append( (math.cos(angle) * major_radius, math.sin(angle) * major_radius, 0.0) )
        main_loop_verts.append(main_loop_verts[0]) # <<< Add first point again at the end
    loops_verts.append(main_loop_verts)

    # 2. Minor Loops (Minor Radius)
    if minor_segments >= 3 and minor_radius > 1e-5:
        # Centers for the minor loops (Unchanged)
        center_top    = mathutils.Vector((0, major_radius, 0))
        center_bottom = mathutils.Vector((0, -major_radius, 0))
        center_right  = mathutils.Vector((major_radius, 0, 0))
        center_left   = mathutils.Vector((-major_radius, 0, 0))

        # --- Revised Minor Loop Generation ---
        def generate_cross_section_loop(center, tangent_to_main_loop):
            verts = []
            # ... (calculate n, t1, t2 basis vectors) ...
            n = tangent_to_main_loop.normalized(); t1 = Vector((0.0, 0.0, 1.0)); t2 = n.cross(t1).normalized();
            # --- Check for t2 validity ---
            if t2.length < 0.1: # If n was parallel to t1 (world Z)
                # Use a different 'up' vector for cross product if tangent is Z-aligned (shouldn't happen for XY main loop)
                t2 = n.cross(Vector((0.0, 1.0, 0.0))).normalized() # Use World Y as temp 'up'
            t1 = t2.cross(n).normalized() # Recalculate t1
            # --------------------------

            for i in range(minor_segments): # Generate N points
                angle = (i / minor_segments) * 2 * math.pi
                offset = (t1 * math.cos(angle) + t2 * math.sin(angle)) * minor_radius
                verts.append( center + offset )
            verts.append(verts[0])
            return verts

        # Calculate tangents at the cardinal points of the main XY loop
        # Top: Tangent is along +X direction
        tangent_top = Vector((1.0, 0.0, 0.0))
        loops_verts.append(generate_cross_section_loop(center_top, tangent_top))

        # Bottom: Tangent is along -X direction
        tangent_bottom = Vector((-1.0, 0.0, 0.0))
        loops_verts.append(generate_cross_section_loop(center_bottom, tangent_bottom))

        # Right: Tangent is along +Y direction
        tangent_right = Vector((0.0, 1.0, 0.0))
        loops_verts.append(generate_cross_section_loop(center_right, tangent_right))

        # Left: Tangent is along -Y direction
        tangent_left = Vector((0.0, -1.0, 0.0))
        loops_verts.append(generate_cross_section_loop(center_left, tangent_left))
        # --- End Revised Minor Loop Generation ---

    else: # Add placeholders if no minor loops generated
         loops_verts.extend([[], [], [], []])

    return loops_verts

def create_corner_arc_verts(corner_point, axis1_offset, axis2_offset, axis3_offset, radius, segments):
    """
    Generates local vertices for one rounded corner arc.
    - corner_point: The original sharp corner vertex (e.g., (-0.5, -0.5, -0.5))
    - axis1/2/3_offset: Vectors pointing AWAY from the corner along the cube edges,
                       magnitude equal to the radius.
                       (e.g., for (-0.5,-0.5,-0.5) corner, offsets are (+R,0,0), (0,+R,0), (0,0,+R))
    - radius: The rounding radius.
    - segments: Number of vertices for the 90-degree arc.
    Returns a list of vertices for the arc.
    """
    if segments < 1: segments = 1
    verts = []
    # Center of the arc's circle, offset from the corner along all three axes
    arc_center = corner_point + axis1_offset + axis2_offset + axis3_offset
    # Start and end points of the arc (relative to the center)
    start_vec = -axis1_offset # Vector from center to point on edge 1
    end_vec = -axis2_offset   # Vector from center to point on edge 2
    # Need a third vector to define the plane if axis3 is involved? Let's simplify.

    # --- Alternative: Calculate points on the arc directly ---
    # Points where the rounding meets the straight edges
    p1 = corner_point + axis1_offset
    p2 = corner_point + axis2_offset
    p3 = corner_point + axis3_offset

    # We need arcs connecting p1-p2, p2-p3, p3-p1 effectively.
    # Let's generate 3 arcs per corner.

    arc_verts_12 = [] # Arc between edge 1 and edge 2
    arc_verts_23 = [] # Arc between edge 2 and edge 3
    arc_verts_31 = [] # Arc between edge 3 and edge 1

    # Center of the arc on the face defined by axis1 and axis2
    center_12 = corner_point + axis1_offset + axis2_offset
    v1 = p1 - center_12 # Vector from center to p1 (-axis2_offset)
    v2 = p2 - center_12 # Vector from center to p2 (-axis1_offset)
    # Interpolate angle from v1 to v2
    for i in range(segments + 1):
        angle = (i / segments) * (math.pi / 2.0) # 90 degrees
        # Simple lerp might not be circular, need proper rotation
        # Rotate v1 towards v2 around the normal (axis3 direction)
        # This gets complicated quickly.

    # --- Simpler Visualization: Draw straight chamfers instead of arcs ---
    # Connect p1, p2, p3 with lines to form a small triangle at the corner
    # return [p1, p2, p3] # For LINE_LOOP triangle
    # Or return lines: [p1, p2, p2, p3, p3, p1] # For LINES

    # --- Even Simpler: Just draw the points where rounding starts ---
    # return [p1, p2, p3] # Just the points (not visually connected)

    # --- Let's stick to the chamfer idea for simplicity ---
    chamfer_lines = [p1, p2, p2, p3, p3, p1]
    return chamfer_lines

def create_unit_rounded_rectangle_plane(local_right, local_up, radius, segments_per_corner):
    """
    Generates local vertices for a unit rounded rectangle (-0.5 to 0.5)
    centered at the origin, lying in the plane defined by local_right and local_up.
    Radius is clamped between 0 and 0.5. Returns list of Vector objects.
    """
    # Unit dimensions
    width = 1.0
    height = 1.0
    half_w, half_h = 0.5, 0.5
    center = mathutils.Vector((0.0, 0.0, 0.0)) # Ensure Vector type

    # Ensure inputs are Vectors
    local_right = mathutils.Vector(local_right)
    local_up = mathutils.Vector(local_up)

    # Clamp radius (0 to 0.5 for unit square)
    radius = max(0.0, min(radius, 0.5))

    # --- Corrected simple rectangle vertex generation ---
    if radius <= 0.0001:
        # Generate 4 corners directly in LINE_LOOP order
        tr = center + (local_right * half_w) + (local_up * half_h)
        tl = center + (-local_right * half_w) + (local_up * half_h)
        bl = center + (-local_right * half_w) - (local_up * half_h)
        br = center + (local_right * half_w) - (local_up * half_h)
        # Return list of Vector objects in correct order
        return [tr, tl, bl, br]
    # --- End Correction ---

    # Rounded logic
    if segments_per_corner < 1: segments_per_corner = 1
    inner_w, inner_h = half_w - radius, half_h - radius
    center_tr = center + (local_right * inner_w) + (local_up * inner_h)
    center_tl = center + (-local_right * inner_w) + (local_up * inner_h)
    center_bl = center + (-local_right * inner_w) - (local_up * inner_h)
    # --- FIX TYPO: Use local_right ---
    center_br = center + (local_right * inner_w) - (local_up * inner_h)
    # --- END FIX ---

    vertices = []
    delta_angle = (math.pi / 2.0) / segments_per_corner

    # Generate points for each corner arc, appending Vector objects
    # Top Right corner
    for i in range(segments_per_corner + 1):
        angle = i * delta_angle
        offset = (local_right * math.cos(angle) + local_up * math.sin(angle)) * radius
        vertices.append(center_tr + offset) # Already Vector + Vector
    # Top Left corner
    for i in range(1, segments_per_corner + 1):
        angle = (math.pi / 2.0) + (i * delta_angle)
        offset = (local_right * math.cos(angle) + local_up * math.sin(angle)) * radius
        vertices.append(center_tl + offset)
    # Bottom Left corner
    for i in range(1, segments_per_corner + 1):
        angle = math.pi + (i * delta_angle)
        offset = (local_right * math.cos(angle) + local_up * math.sin(angle)) * radius
        vertices.append(center_bl + offset)
    # Bottom Right corner
    for i in range(1, segments_per_corner + 1):
        angle = (3 * math.pi / 2.0) + (i * delta_angle)
        offset = (local_right * math.cos(angle) + local_up * math.sin(angle)) * radius
        vertices.append(center_br + offset)

    # Do NOT add duplicate vertex for LINE_LOOP, it closes automatically.
    return vertices # Return list of Vector objects


def create_unit_cylinder_cap_vertices(segments):
    """Generates local vertices for top and bottom caps of a unit cylinder."""
    top_verts = []
    bot_verts = []
    radius = 0.5 # Unit cylinder radius
    half_height = 0.5 # Unit cylinder half-height
    if segments >= 3:
        for i in range(segments):
            angle = (i / segments) * 2 * math.pi
            x = math.cos(angle) * radius
            y = math.sin(angle) * radius
            top_verts.append( (x, y, half_height) ) # Z = +0.5
            bot_verts.append( (x, y, -half_height) ) # Z = -0.5
    return top_verts, bot_verts

def offset_vertices(vertices, camera_loc, offset_factor):
    """Offsets a list of vertices slightly towards the camera."""
    offset_verts = []
    if not vertices:
        return []
    for v in vertices:
        # Ensure v is a Vector
        v_vec = mathutils.Vector(v)
        view_dir_unnormalized = camera_loc - v_vec
        # Avoid division by zero if camera is exactly at vertex
        if view_dir_unnormalized.length_squared > 1e-9:
             view_dir = view_dir_unnormalized.normalized()
             offset_v = v_vec + view_dir * offset_factor
             offset_verts.append(offset_v)
        else:
             # Cannot calculate offset, just use original vertex
             offset_verts.append(v_vec)
    return offset_verts

def offset_vertices(vertices, camera_loc, offset_factor):
    """Offsets a list of vertices slightly towards the camera."""
    offset_verts = []
    if not vertices:
        return []
    for v in vertices:
        # Ensure v is a Vector
        v_vec = mathutils.Vector(v)
        view_dir_unnormalized = camera_loc - v_vec
        # Avoid division by zero if camera is exactly at vertex
        if view_dir_unnormalized.length_squared > 1e-9:
             view_dir = view_dir_unnormalized.normalized()
             offset_v = v_vec + view_dir * offset_factor
             offset_verts.append(offset_v)
        else:
             # Cannot calculate offset, just use original vertex
             offset_verts.append(v_vec)
    return offset_verts

def ff_draw_callback():
    """Draw callback function - Iterates through scene objects using bpy.context"""
    # print("--- FieldForge Draw Callback Running (using bpy.context) ---") # Optional

    # --- Use bpy.context explicitly ---
    context = bpy.context
    # ... (Keep the robust context checks for scene, space_data, region_3d) ...
    scene = getattr(context, 'scene', None)
    space_data = None
    area = context.area
    # ... (logic to find space_data and region_3d) ...
    if area and area.type == 'VIEW_3D': space_data = area.spaces.active
    if not space_data: # Fallback
        for area_iter in context.screen.areas:
             if area_iter.type == 'VIEW_3D':
                 space_data = area_iter.spaces.active
                 break
    region_3d = getattr(space_data, 'region_3d', None) if space_data else None

    if not scene or not region_3d: return # Exit if context is incomplete

    # --- Get Camera Location (Needed for Sphere) ---
    try:
        view_matrix_inv = region_3d.view_matrix.inverted()
        camera_location = view_matrix_inv.translation
    except Exception:
        # print("DBG Draw Skip: Cannot get camera location") # Optional
        return
    # ---------------------------

    # --- GPU Setup ---
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    old_blend = gpu.state.blend_get()
    old_line_width = gpu.state.line_width_get()
    old_depth_test = gpu.state.depth_test_get()

    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(1.0)
    gpu.state.depth_test_set('LESS_EQUAL') # Keep depth test enabled
    #gpu.state.depth_test_set(old_depth_test)
    shader.bind()
    # ---------------------------------------------

    DEPTH_OFFSET_FACTOR = 0.1

    # --- Iterate through scene objects ---
    for obj in scene.objects:
        # --- Manual Visibility & Filtering (Keep checks) ---
        # --- Filtering (Keep as is) ---
        try:
            if not obj or not obj.visible_get(): continue
        except ReferenceError: continue
        if not is_sdf_source(obj): continue
        parent_bounds = find_parent_bounds(obj); # ... (check parent_bounds) ...
        if not parent_bounds: continue
        if not get_bounds_setting(parent_bounds, "sdf_show_source_empties"): continue
        sdf_type_prop = obj.get("sdf_type", "NONE"); # ... (check sdf_type_prop) ...
        if sdf_type_prop == "NONE": continue

        is_negative = obj.get("sdf_is_negative", False)
        color = (1.0, 0.2, 0.2, 0.8) if is_negative else (0.9, 0.9, 0.9, 0.8)

        obj_matrix = obj.matrix_world
        obj_location = obj_matrix.translation
        obj_scale_vec = obj_matrix.to_scale()
        avg_scale = max(1e-5, (abs(obj_scale_vec.x) + abs(obj_scale_vec.y) + abs(obj_scale_vec.z)) / 3.0)

        batches_to_draw = []
        mat = obj_matrix


        if sdf_type_prop == "sphere":
            primitive_type = 'LINE_LOOP'
            world_verts = []
            cam_right_vector = Vector((1.0, 0.0, 0.0)) # Initialize with defaults
            cam_up_vector = Vector((0.0, 1.0, 0.0))    # In case try block fails
            try: # Cam vectors
                direction = (camera_location - obj_location).normalized()
                if direction.length < 0.0001: direction = mathutils.Vector((0.0, 0.0, 1.0))
                world_up = mathutils.Vector((0.0, 0.0, 1.0))
                if abs(direction.dot(world_up)) > 0.999: world_up = mathutils.Vector((0.0, 1.0, 0.0)).normalized()
                cam_right_vector = direction.cross(world_up).normalized()
                cam_up_vector = cam_right_vector.cross(direction).normalized()
            except ValueError:
                # Handle potential errors during vector calculation if needed
                print(f"FF Draw Warn: Could not calculate camera vectors for sphere {obj.name}")
                pass # Use default vectors initialized above

            radius = 0.5 * avg_scale
            segments = 24

            # Generate world verts directly using calculated vectors
            for i in range(segments):
                angle = (i / segments) * 2 * math.pi
                # --- FIX: Replace ellipsis with actual vectors ---
                offset = (cam_right_vector * math.cos(angle) + cam_up_vector * math.sin(angle)) * radius
                # -------------------------------------------------
                world_verts.append(obj_location + offset)

            if world_verts:
                # Offset Sphere Vertices
                offset_verts = offset_vertices(world_verts, camera_location, DEPTH_OFFSET_FACTOR)
                try:
                    batch = batch_for_shader(shader, primitive_type, {"pos": offset_verts}) # Use offset_verts
                    batches_to_draw.append((batch, color))
                except Exception as e: print(f"FF Draw Error: Sphere batch failed for {obj.name}: {e}")
        elif sdf_type_prop == "cube":
            primitive_type = 'LINES'
            indices = unit_cube_indices # Indices for drawing lines

            # 1. Generate local unit vertices as Vectors
            local_verts_vectors = [mathutils.Vector(v) for v in unit_cube_verts]
            world_verts = [] # To store transformed vertices

            # 2. Transform local vertices to world space
            for v_local in local_verts_vectors:
                v4 = v_local.to_4d(); v4.w = 1.0 # Convert to 4D for matrix multiplication
                v_world_4d = mat @ v4          # Apply object's world matrix
                world_verts.append(v_world_4d.xyz) # Store 3D world coordinate

            # 3. Check if transformation produced vertices
            if world_verts:
                # --- 4. Offset Vertices towards camera ---
                offset_verts = offset_vertices(world_verts, camera_location, DEPTH_OFFSET_FACTOR)
                # ----------------------------------------

                # 5. Create batch using OFFSETTED vertices
                try:
                    batch = batch_for_shader(shader, primitive_type, {"pos": offset_verts}, indices=indices) # Use offset_verts
                    batches_to_draw.append((batch, color))
                except Exception as e: print(f"FF Draw Error: Cube batch failed for {obj.name}: {e}")
        elif sdf_type_prop == "torus":
            primitive_type = 'LINE_LOOP'
            # --- FIX: Define Unit Radii Here ---
            unit_major_r = 0.35 # Default major radius for unit torus visual
            unit_minor_r = 0.15 # Default minor radius for unit torus visual
            # ---------------------------------
            main_segments = 32
            minor_segments = 12

            # Generate all local loops USING UNIT RADII
            local_loops = create_torus_visual_loops(unit_major_r, unit_minor_r, main_segments, minor_segments)

            # Transform and create batches (Logic Unchanged)
            mat = obj_matrix
            for local_verts in local_loops:
                if not local_verts: continue
                world_verts = []
                for v_local in local_verts:
                    v_vec = mathutils.Vector(v_local)
                    v4 = v_vec.to_4d(); v4.w = 1.0
                    v_world_4d = mat @ v4
                    world_verts.append(v_world_4d.xyz)

                if world_verts:
                    # Offset Torus Loop Vertices
                    offset_verts = offset_vertices(world_verts, camera_location, DEPTH_OFFSET_FACTOR)
                    try:
                        batch = batch_for_shader(shader, primitive_type, {"pos": offset_verts}) # Use offset_verts
                        batches_to_draw.append((batch, color))
                    except Exception as e: print(f"FF Draw Error: Torus loop batch failed for {obj.name}: {e}")
                    except Exception as e: print(f"FF Draw Error: Torus loop batch failed for {obj.name}: {e}")

        elif sdf_type_prop == "cylinder":
            segments = 16
            local_top_verts, local_bot_verts = create_unit_cylinder_cap_vertices(segments)
            world_top_verts = []; world_bot_verts = []
            # Transform top cap
            if local_top_verts:
                for v_local in local_top_verts: v4 = mathutils.Vector(v_local).to_4d(); v4.w = 1.0; v_world_4d = mat @ v4; world_top_verts.append(v_world_4d.xyz)
                # --- Offset Top Cap Vertices ---
                offset_top_verts = offset_vertices(world_top_verts, camera_location, DEPTH_OFFSET_FACTOR)
                # ------------------------------
                try: batch = batch_for_shader(shader, 'LINE_LOOP', {"pos": offset_top_verts}); batches_to_draw.append((batch, color)) # Use offset
                except Exception as e: print(f"FF Draw Error: Cyl Top Cap batch failed for {obj.name}: {e}")
            # Transform bottom cap
            if local_bot_verts:
                for v_local in local_bot_verts: v4 = mathutils.Vector(v_local).to_4d(); v4.w = 1.0; v_world_4d = mat @ v4; world_bot_verts.append(v_world_4d.xyz)
                # --- Offset Bottom Cap Vertices ---
                offset_bot_verts = offset_vertices(world_bot_verts, camera_location, DEPTH_OFFSET_FACTOR)
                # -------------------------------
                try: batch = batch_for_shader(shader, 'LINE_LOOP', {"pos": offset_bot_verts}); batches_to_draw.append((batch, color)) # Use offset
                except Exception as e: print(f"FF Draw Error: Cyl Bot Cap batch failed for {obj.name}: {e}")
            # Side lines
            if world_top_verts and world_bot_verts: # Use original world verts for calculation
                try: # Calculate side line endpoints using original world verts ...
                    # ... (calculate side1_top, side1_bot, side2_top, side2_bot using non-offset cap centers etc.) ...
                    world_x_axis = mat.col[0].xyz; world_y_axis = mat.col[1].xyz; world_z_axis = mat.col[2].xyz; world_location = mat.translation
                    radius_x = world_x_axis.length * 0.5; radius_y = world_y_axis.length * 0.5; world_radius = (radius_x + radius_y) / 2.0
                    view_vec = (camera_location - world_location); z_axis_norm = world_z_axis.normalized(); view_vec_norm = view_vec.normalized()
                    side_vector = None; # ... calc side_vector ...
                    if abs(z_axis_norm.dot(view_vec_norm)) > 0.999: side_vector = world_x_axis.normalized()
                    else: side_vector = world_z_axis.cross(view_vec).normalized()
                    center_top_world = world_location + world_z_axis * 0.5; center_bot_world = world_location - world_z_axis * 0.5
                    side_offset = side_vector * world_radius
                    side1_top = center_top_world + side_offset; side1_bot = center_bot_world + side_offset
                    side2_top = center_top_world - side_offset; side2_bot = center_bot_world - side_offset

                    side_lines_verts = [side1_top, side1_bot, side2_top, side2_bot]
                    # --- Offset Side Line Vertices ---
                    offset_side_verts = offset_vertices(side_lines_verts, camera_location, DEPTH_OFFSET_FACTOR)
                    # --------------------------------
                    batch = batch_for_shader(shader, 'LINES', {"pos": offset_side_verts}); batches_to_draw.append((batch, color)) # Use offset
                except Exception as e: print(f"FF Draw Error: Calculating Cyl Side Lines failed for {obj.name}: {e}")

        elif sdf_type_prop == "cone":
            segments = 16

            # --- Use ORIGINAL Unit Dimensions for Visual Guide ---
            local_radius = 0.5      # Unit Cone Radius
            local_height = 1.0      # Unit Cone Height
            local_apex_z = local_height # Apex Z coordinate (relative to base at 0)
            local_base_z = 0.0      # Base circle at Z=0
            # ----------------------------------------------------

            # 1. Generate and transform base cap vertices (at Z=0, using local_radius)
            local_bot_verts = []
            if segments >= 3:
                for i in range(segments):
                    angle = (i / segments) * 2 * math.pi
                    # Use local_radius (0.5) here
                    x = math.cos(angle) * local_radius
                    y = math.sin(angle) * local_radius
                    local_bot_verts.append( (x, y, local_base_z) ) # Use Z=0

            world_bot_verts = []
            if local_bot_verts:
                for v_local in local_bot_verts:
                    v4 = mathutils.Vector(v_local).to_4d(); v4.w = 1.0
                    v_world_4d = mat @ v4; world_bot_verts.append(v_world_4d.xyz)
                # Draw Base Cap (Offset Vertices)
                offset_bot_verts = offset_vertices(world_bot_verts, camera_location, DEPTH_OFFSET_FACTOR)
                try: batch = batch_for_shader(shader, 'LINE_LOOP', {"pos": offset_bot_verts}); batches_to_draw.append((batch, color))
                except Exception as e: print(f"FF Draw Error: Cone Bot Cap batch failed for {obj.name}: {e}")

            # 2. Transform Apex (using local_apex_z = local_height = 1.0)
            local_apex = mathutils.Vector((0.0, 0.0, local_apex_z))
            apex4 = local_apex.to_4d(); apex4.w = 1.0
            world_apex = (mat @ apex4).xyz
            # Offset the single apex point
            offset_apex = offset_vertices([world_apex], camera_location, DEPTH_OFFSET_FACTOR)[0]


            # 3. Calculate side line base points (Billboard effect)
            if world_bot_verts:
                try:
                    # Get World Space Axes, Radius, Base Center
                    world_x_axis = mat.col[0].xyz; world_y_axis = mat.col[1].xyz; world_z_axis = mat.col[2].xyz; world_location = mat.translation

                    # Calculate World radius based on transformed axes length * local_radius (0.5)
                    radius_x = world_x_axis.length * local_radius # Use 0.5
                    radius_y = world_y_axis.length * local_radius # Use 0.5
                    world_radius = (radius_x + radius_y) / 2.0

                    # World Base Cap Center IS the object location
                    center_bot_world = world_location

                    # Calculate Billboard Direction (Same logic)
                    view_vec = (camera_location - world_location); # ... calc side_vector ...
                    z_axis_norm = world_z_axis.normalized(); view_vec_norm = view_vec.normalized()
                    side_vector = None
                    if abs(z_axis_norm.dot(view_vec_norm)) > 0.999: side_vector = world_x_axis.normalized()
                    else: side_vector = world_z_axis.cross(view_vec).normalized()

                    # Calculate offset and the two base points
                    side_offset = side_vector * world_radius
                    side1_base = center_bot_world + side_offset
                    side2_base = center_bot_world - side_offset

                    # Offset the base points
                    offset_side1_base = offset_vertices([side1_base], camera_location, DEPTH_OFFSET_FACTOR)[0]
                    offset_side2_base = offset_vertices([side2_base], camera_location, DEPTH_OFFSET_FACTOR)[0]

                    # Create batch using OFFSET apex and OFFSET base points
                    side_lines_verts = [offset_apex, offset_side1_base, offset_apex, offset_side2_base]
                    batch = batch_for_shader(shader, 'LINES', {"pos": side_lines_verts})
                    batches_to_draw.append((batch, color))

                except Exception as e:
                     print(f"FF Draw Error: Calculating Cone Side Lines failed for {obj.name}: {e}")
                     # import traceback; traceback.print_exc()

        elif sdf_type_prop == "rounded_box":
             primitive_type = 'LINE_LOOP'; corner_segments = 6; # ... get radius ...
             ui_radius = obj.get("sdf_round_radius", 0.1); internal_draw_radius = max(0.0, min(ui_radius * 0.5, 0.5))
             local_x = Vector((1.0, 0.0, 0.0)); local_y = Vector((0.0, 1.0, 0.0)); local_z = Vector((0.0, 0.0, 1.0)); local_loops = []
             local_loops.append( create_unit_rounded_rectangle_plane(local_x, local_y, internal_draw_radius, corner_segments) ) # XY
             local_loops.append( create_unit_rounded_rectangle_plane(local_y, local_z, internal_draw_radius, corner_segments) ) # YZ
             local_loops.append( create_unit_rounded_rectangle_plane(local_x, local_z, internal_draw_radius, corner_segments) ) # XZ

             # --- Transform and create batches ---
             for local_verts in local_loops: # Process each loop (XY, YZ, XZ)
                 if not local_verts: continue

                 # --- FIX: Initialize world_verts for EACH loop ---
                 world_verts = []
                 # -----------------------------------------------

                 # Transform this loop's unit vertices
                 for v_local in local_verts: # v_local is already a Vector
                     v4 = v_local.to_4d(); v4.w = 1.0
                     v_world_4d = mat @ v4
                     world_verts.append(v_world_4d.xyz) # Append to the list for this loop

                 if world_verts:
                     # Offset Rounded Box Loop Vertices
                     offset_verts = offset_vertices(world_verts, camera_location, DEPTH_OFFSET_FACTOR)
                     try: # Create batch for this specific loop
                         batch = batch_for_shader(shader, primitive_type, {"pos": offset_verts}) # Use offset
                         batches_to_draw.append((batch, color))
                     except Exception as e: print(f"FF Draw Error: RndBox loop batch failed for {obj.name}: {e}")



        # --- Draw all batches accumulated for THIS object ---
        for batch, batch_color in batches_to_draw:
            try:
                shader.uniform_float("color", batch_color)
                batch.draw(shader)
            except Exception as e:
                 print(f"FieldForge Draw Error: Final Batch Draw failed for {obj.name}: {e}")


    # --- Restore GPU State ---
    gpu.state.line_width_set(old_line_width)
    gpu.state.blend_set(old_blend)
    gpu.state.depth_test_set(old_depth_test)


# --- NEW: Helper to tag redraw ---
def tag_redraw_all_view3d():
    """Forces redraw of all 3D views."""
    if not bpy.context or not bpy.context.window_manager: return
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception as e:
        # Can sometimes fail during startup/shutdown
        # print(f"FieldForge WARN: Error tagging redraw: {e}")
        pass

# --- Initial Update Check on Load/Register ---
# (Keep initial_update_check_all as it was)
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
             # Make sure scene is valid
             scene = context.scene
             if scene:
                bpy.app.timers.register(
                     lambda name=bounds_obj.name, scn=scene: check_and_trigger_update(scn, name, "initial_check"),
                     first_interval=0.1 + count * 0.05 # Stagger checks slightly
                )
                count += 1
        except Exception as e:
            print(f"FieldForge ERROR: Failed to schedule initial check for {bounds_obj.name}: {e}")
    if count > 0:
        print(f"FieldForge: Scheduled initial checks for {count} bounds systems.")
        # Also trigger an initial redraw after checks are scheduled
        bpy.app.timers.register(tag_redraw_all_view3d, first_interval=0.2 + count * 0.05)

    return None # Timer function should return None

# --- Registration ---

classes = (
    # Operators (Same as before)
    OBJECT_OT_add_sdf_bounds,
    OBJECT_OT_add_sdf_cube_source,
    OBJECT_OT_add_sdf_sphere_source,
    OBJECT_OT_add_sdf_cylinder_source,
    OBJECT_OT_add_sdf_cone_source,
    OBJECT_OT_add_sdf_torus_source,
    OBJECT_OT_add_sdf_rounded_box_source,
    OBJECT_OT_sdf_manual_update,
    # Panels (Same as before)
    VIEW3D_PT_fieldforge_main,
    # Menus (Same as before)
    VIEW3D_MT_add_sdf,
)

# Store handler reference for safe removal (depsgraph handler)
_handler_ref = None
# Draw handler reference is stored in _draw_handle (global)

def register():
    """Registers all addon classes, handlers, and menu items."""
    global _handler_ref, _draw_handle # <<< Include draw handle
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
        bpy.app.timers.register(initial_update_check_all, first_interval=1.0)

    # --- NEW: Register Draw Handler ---
    if _draw_handle is None:
        try:
            _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
                ff_draw_callback, # The function object
                (),               # Empty tuple for args
                'WINDOW', 'POST_VIEW'
            )
            print("FieldForge: Custom Draw Handler Registered.")
        except Exception as e:
            print(f"FieldForge ERROR: Failed to register draw handler: {e}")
        # --- End: Register Draw Handler ---

    print(f"FieldForge: Registered. (libfive available: {libfive_available})")
    tag_redraw_all_view3d()


def unregister():
    """Unregisters all addon classes, handlers, and menu items."""
    global _handler_ref, _draw_handle # <<< Include draw handle

    # --- NEW: Unregister Draw Handler ---
    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
            print("FieldForge: Custom Draw Handler Unregistered.")
        except ValueError:
             pass # Ignore error if already removed
        except Exception as e:
             print(f"FieldForge WARN: Error removing draw handler: {e}")
        _draw_handle = None
    # --- End: Unregister Draw Handler ---

    # Unregister depsgraph handler
    if _handler_ref:
        handler_list = bpy.app.handlers.depsgraph_update_post
        if _handler_ref in handler_list:
            try:
                handler_list.remove(_handler_ref)
            except ValueError:
                pass # Handler already removed
            except Exception as e:
                print(f"FieldForge WARN: Error removing depsgraph handler: {e}")
        _handler_ref = None

    # Remove menu item
    try:
        bpy.types.VIEW3D_MT_add.remove(menu_func)
    except Exception:
        pass # Menu item might already be removed

    # Cancel all active timers managed by the addon
    global _debounce_timers
    if bpy.app.timers: # Check if timers system is still available (might not be during shutdown)
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
    tag_redraw_all_view3d() # Force redraw after unregistration


# --- Main Execution Block (for direct script execution or reload) ---
if __name__ == "__main__":
    # Standard Blender script reload pattern: unregister first, then register
    try:
        unregister()
    except Exception as e:
        print(f"FieldForge: Error during unregister on reload: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            register()
        except Exception as e:
            print(f"FieldForge: Error during register on reload: {e}")
            import traceback
            traceback.print_exc()
