# FieldForge/core/sdf_logic.py

"""
Core SDF Shape Construction Logic for FieldForge Addon.

Handles recursive processing of the Blender object hierarchy to build
a combined libfive.Shape based on object properties and transformations.
"""

import math
import bpy
# Attempt to import libfive - success depends on setup in root __init__.py
try:
    import libfive.stdlib as lf
    import libfive.shape # Access Shape.X etc.
    _lf_imported_ok = True
except ImportError:
    print("FieldForge WARN (sdf_logic.py): libfive modules not found during import.")
    _lf_imported_ok = False
    # Define dummy lf object so functions don't immediately crash on load,
    # although they will fail if called without libfive.
    class LFDummy:
        def __getattr__(self, name):
            raise RuntimeError("libfive not available")
    lf = LFDummy()
    class ShapeDummy:
        @staticmethod
        def X(): raise RuntimeError("libfive not available")
        @staticmethod
        def Y(): raise RuntimeError("libfive not available")
        @staticmethod
        def Z(): raise RuntimeError("libfive not available")
    libfive = type('module', (), {'shape': ShapeDummy})()


from mathutils import Vector, Matrix

# Use relative imports assuming this file is in FieldForge/core/
from .. import constants
from .. import utils # For is_sdf_source, is_valid_2d_loft_source


def reconstruct_shape(obj) -> lf.Shape | None:
    """
    Reconstructs a UNIT libfive shape based on the object's 'sdf_type' property.
    Scaling and transformation are handled separately via the object's matrix.
    Extrusion for 2D shapes is handled later in process_sdf_hierarchy.

    Returns a libfive Shape or lf.emptiness() on error/unknown type.
    """
    if not _lf_imported_ok or not obj:
        return lf.emptiness() if _lf_imported_ok else None # Return dummy empty or None

    sdf_type = obj.get("sdf_type", "")
    shape = None
    unit_radius = 0.5 # Standard radius for shapes like cylinder/cone base/sphere/circle
    unit_height = 1.0 # Standard height for shapes like cylinder/cone
    half_size = 0.5 # Half-dimension for unit cube/box related calculations

    try:
        if sdf_type == "cube":
            shape = lf.cube_centered((2 * half_size, 2 * half_size, 2 * half_size))
        elif sdf_type == "sphere":
            shape = lf.sphere(unit_radius)
        elif sdf_type == "cylinder":
            shape = lf.cylinder_z(unit_radius, unit_height, base=(0, 0, -half_size))
        elif sdf_type == "cone":
            if unit_height <= 1e-6: # Avoid division by zero if height is near zero
                 # Fallback to a flat disk or emptiness if height is zero.
                 # For simplicity, return emptiness; a disk could be lf.circle extruded slightly.
                 print(f"FieldForge WARN (reconstruct_shape): Cone height near zero for {obj.name}. Returning empty shape.")
                 return lf.emptiness()

            # Denominator for scaling factor
            sqrt_term = math.sqrt(unit_radius**2 + unit_height**2)
            if sqrt_term <= 1e-6: # Avoid division by zero if unit_radius and unit_height are both zero
                 print(f"FieldForge WARN (reconstruct_shape): Cone radius and height near zero for {obj.name}. Returning empty shape.")
                 return lf.emptiness()

            cone_param_radius = (unit_radius**2) / sqrt_term
            cone_param_height = (unit_height * unit_radius) / sqrt_term
            
            shape = lf.cone_z(cone_param_radius, cone_param_height, base=(0, 0, 0.0))
        elif sdf_type == "torus":
            default_major = constants.DEFAULT_SOURCE_SETTINGS["sdf_torus_major_radius"]
            default_minor = constants.DEFAULT_SOURCE_SETTINGS["sdf_torus_minor_radius"]
            major_r_prop = obj.get("sdf_torus_major_radius", default_major)
            minor_r_prop = obj.get("sdf_torus_minor_radius", default_minor)
            major_r = max(0.01, major_r_prop); minor_r = max(0.005, minor_r_prop)
            minor_r = min(minor_r, major_r - 1e-5)
            shape = lf.torus_z(major_r, minor_r, center=(0,0,0))

        elif sdf_type == "rounded_box":
            roundness_prop = obj.get("sdf_round_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_round_radius"])
            internal_sdf_radius = min(roundness_prop, 0.5) * half_size / 0.5
            internal_sdf_radius = min(max(roundness_prop, 0.0), 1.0) * half_size
            effective_prop_value = min(max(roundness_prop, 0.0), 0.5)
            internal_sdf_radius = effective_prop_value * (half_size / 0.5)
            corner_a = (-half_size, -half_size, -half_size)
            corner_b = ( half_size,  half_size,  half_size)
            if internal_sdf_radius <= 1e-5:
                shape = lf.cube_centered((2 * half_size, 2 * half_size, 2 * half_size))
            else:
                safe_sdf_radius = min(internal_sdf_radius, half_size - 1e-5)
                shape = lf.rounded_box(corner_a, corner_b, safe_sdf_radius)
        elif sdf_type == "circle":
            shape = lf.circle(unit_radius, center=(0, 0))
        elif sdf_type == "ring":
            inner_r_prop = obj.get("sdf_inner_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_inner_radius"])
            # Ensure inner radius is relative to the unit_radius (0.5)
            safe_inner_r = max(0.0, min(inner_r_prop * (unit_radius/0.5), unit_radius - 1e-5))
            shape = lf.ring(unit_radius, safe_inner_r, center=(0, 0))
        elif sdf_type == "polygon":
            sides = obj.get("sdf_sides", constants.DEFAULT_SOURCE_SETTINGS["sdf_sides"])
            safe_n = max(3, sides)
            shape = lf.polygon(unit_radius, safe_n, center=(0, 0))
        elif sdf_type == "half_space":
            shape = lf.half_space((0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        else:
            print(f"FieldForge WARN (reconstruct_shape): Unknown sdf_type '{sdf_type}' for {obj.name}")
            return lf.emptiness()

    except Exception as e:
        print(f"FieldForge ERROR (reconstruct_shape): Error creating unit shape for {obj.name} ({sdf_type}): {e}")
        return lf.emptiness()

    return shape


def apply_blender_transform_to_sdf(shape, obj_matrix_world_inv: Matrix) -> lf.Shape | None:
    """
    Applies Blender object's inverted world transform to a libfive shape using remap.
    Returns lf.emptiness() on error.
    """
    if not _lf_imported_ok: return None
    if shape is None: return lf.emptiness() # Cannot transform None
    # Check if shape is already emptiness to avoid unnecessary work
    if hasattr(lf, 'emptiness') and shape is lf.emptiness(): return shape

    if obj_matrix_world_inv is None:
        print(f"FieldForge WARN (apply_transform): Received None matrix_world_inv.")
        return lf.emptiness() # Cannot transform with None matrix

    # Symbolic world coordinates
    X = libfive.shape.Shape.X()
    Y = libfive.shape.Shape.Y()
    Z = libfive.shape.Shape.Z()

    # Calculate remapped coordinates using inverse matrix
    mat_inv = obj_matrix_world_inv
    try:
        # new_coord = mat_inv * old_coord_vec (where old_coord_vec is [X, Y, Z, 1])
        # Note: libfive remap takes expressions for new x, y, z based on old X, Y, Z
        x_p = mat_inv[0][0] * X + mat_inv[0][1] * Y + mat_inv[0][2] * Z + mat_inv[0][3]
        y_p = mat_inv[1][0] * X + mat_inv[1][1] * Y + mat_inv[1][2] * Z + mat_inv[1][3]
        z_p = mat_inv[2][0] * X + mat_inv[2][1] * Y + mat_inv[2][2] * Z + mat_inv[2][3]

        transformed_shape = shape.remap(x_p, y_p, z_p)
    except OverflowError:
         # This can happen with extreme scaling or matrix values
         print(f"FieldForge ERROR (apply_transform): OverflowError during remap (likely extreme transform values).")
         return lf.emptiness()
    except TypeError as e:
        # Catches issues if X,Y,Z or matrix values aren't compatible with libfive ops
        print(f"FieldForge ERROR (apply_transform): TypeError during remap: {e}")
        return lf.emptiness()
    except Exception as e:
        print(f"FieldForge ERROR (apply_transform): Unexpected error during libfive remap: {type(e).__name__} - {e}")
        return lf.emptiness() # Return empty on other errors

    return transformed_shape


def combine_shapes(shape_a, shape_b, blend_factor) -> lf.Shape | None:
    """
    Combines two libfive shapes using union or blend (blend_expt_unit).
    Handles None inputs gracefully. Returns lf.emptiness() on error.
    """
    if not _lf_imported_ok: return None
    # Handle cases where one input is None or represents emptiness
    is_a_empty = shape_a is None or shape_a is lf.emptiness()
    is_b_empty = shape_b is None or shape_b is lf.emptiness()

    if is_a_empty and is_b_empty: return lf.emptiness()
    if is_a_empty: return shape_b
    if is_b_empty: return shape_a

    try:
        safe_blend = max(0.0, float(blend_factor)) # Ensure non-negative float
        # Use blend if factor is significantly > 0
        if safe_blend > constants.CACHE_PRECISION:
            # Clamp factor for blend_expt_unit (expects 0-1 visually)
            # Allow slightly > 1? No, stick to 0-1 range for unit blend.
            clamped_factor = min(1.0, safe_blend)
            return lf.blend_expt_unit(shape_a, shape_b, clamped_factor)
        else:
            # Use sharp union if blend factor is effectively zero
            return lf.union(shape_a, shape_b)
    except Exception as e:
        print(f"FieldForge ERROR (combine_shapes): Error combining shapes: {e}")
        # Fallback strategy: return the first shape? Or emptiness? Emptiness might be safer.
        return lf.emptiness()


def process_sdf_hierarchy(obj: bpy.types.Object, settings: dict) -> lf.Shape | None:
    """
    Recursively processes hierarchy starting from obj.
    Handles loft, shell, array, transform, and CSG operations.
    Returns the final combined libfive.Shape for the subtree rooted at obj,
    or lf.emptiness() if errors occur or the object/subtree is empty/invisible.
    """
    if not _lf_imported_ok: return None
    if not obj: return lf.emptiness()

    # Skip processing if the object itself is hidden (unless it's the root Bounds)
    # Visibility checks for children happen inside the loop.
    is_bounds_root = obj.get(constants.SDF_BOUNDS_MARKER, False)
    if not is_bounds_root and not obj.visible_get(view_layer=bpy.context.view_layer): # Check viewport visibility
         # print(f"DEBUG: Skipping hidden object {obj.name}") # DEBUG
         return lf.emptiness()

    obj_name = obj.name
    processed_children = set() # Keep track of children handled by loft

    # --- Shape Generation for 'obj' ---
    current_shape = lf.emptiness() # Base shape before array/transform
    processed_shape_for_this_obj = lf.emptiness() # Final transformed shape for this obj

    if utils.is_sdf_source(obj):
        # print(f"DEBUG: Processing Source: {obj_name}") # DEBUG
        # --- Stage 1: Check for Loft and Generate Base Shape ---
        loft_child_found = None
        # Check if 'obj' can be a loft parent and find first valid child
        if utils.is_valid_2d_loft_source(obj):
            for child in obj.children:
                if child.name in processed_children: continue
                # Make sure child exists and is visible before considering for loft
                actual_child = bpy.context.scene.objects.get(child.name)
                if not actual_child or not actual_child.visible_get(view_layer=bpy.context.view_layer): continue

                if utils.is_valid_2d_loft_source(child) and child.get("sdf_use_loft", False):
                    loft_child_found = child; break # Process only the first

            if loft_child_found:
                # print(f"  DEBUG: Found Loft pair: Parent={obj_name}, Child={loft_child_found.name}") # DEBUG
                try:
                    shape_a = reconstruct_shape(obj)
                    shape_b = reconstruct_shape(loft_child_found)
                    if shape_a is not None and shape_b is not None and \
                       shape_a is not lf.emptiness() and shape_b is not lf.emptiness():
                        zmin = 0.0 # Parent's local Z
                        zmax = loft_child_found.location.z # Child's local Z relative to parent
                        if abs(zmax - zmin) < 1e-5: zmax = zmin + 1e-5 # Ensure non-zero height
                        if zmin > zmax: zmin, zmax = zmax, zmin # Ensure zmin < zmax
                        # print(f"    DEBUG: Loft zmin={zmin:.3f}, zmax={zmax:.3f}") # DEBUG
                        current_shape = lf.loft(shape_a, shape_b, zmin, zmax)
                        processed_children.add(loft_child_found.name) # Mark child as handled
                    else:
                        print(f"FieldForge WARN (process_hierarchy): Loft failed - could not reconstruct base shapes for {obj_name} or {loft_child_found.name}. Using parent shape.")
                        reconstructed_fallback = reconstruct_shape(obj)
                        if reconstructed_fallback is not None and reconstructed_fallback is not lf.emptiness():
                            current_shape = reconstructed_fallback
                        else:
                            current_shape = lf.emptiness()
                except Exception as e:
                    print(f"FieldForge ERROR (process_hierarchy): Loft failed between {obj_name} and {loft_child_found.name}: {e}")
                    reconstructed_fallback = reconstruct_shape(obj)
                    if reconstructed_fallback is not None and reconstructed_fallback is not lf.emptiness():
                         current_shape = reconstructed_fallback
                    else:
                         current_shape = lf.emptiness()
                    if loft_child_found: processed_children.add(loft_child_found.name)

        # --- Stage 1b: If NOT lofting, get base shape and maybe extrude ---
        if loft_child_found is None:
            reconstructed = reconstruct_shape(obj)
            if reconstructed is None or reconstructed is lf.emptiness():
                current_shape = lf.emptiness()
            else:
                current_shape = reconstructed
                sdf_type = obj.get("sdf_type", "")
                if sdf_type in {"circle", "ring", "polygon"}: # Check if it's a 2D type
                    try:
                        depth = obj.get("sdf_extrusion_depth", 0.1); safe_depth = max(1e-6, depth)
                        zmin_ex = -safe_depth / 2.0; zmax_ex = safe_depth / 2.0
                        current_shape = lf.extrude_z(current_shape, zmin_ex, zmax_ex)
                    except Exception as e: print(f"FieldForge ERROR (process_hierarchy): Extruding {obj_name} failed: {e}"); current_shape = lf.emptiness()

        # --- Stage 2: Apply Shell (to lofted or extruded/base shape) ---
        if obj.get("sdf_use_shell", False) and current_shape is not None and current_shape is not lf.emptiness():
            try:
                offset = obj.get("sdf_shell_offset", 0.1); safe_offset = float(offset)
                if abs(safe_offset) > 1e-6:
                     # print(f"  DEBUG: Shelling {obj_name} by {safe_offset:.3f}") # DEBUG
                     current_shape = lf.shell(current_shape, safe_offset)
            except Exception as e: print(f"FieldForge ERROR (process_hierarchy): Shelling {obj_name} failed: {e}"); current_shape = lf.emptiness()

        # --- Stage 3: Apply Array ---
        if current_shape is not None and current_shape is not lf.emptiness():
            main_mode = obj.get("sdf_main_array_mode", 'NONE')
            if main_mode != 'NONE':
                 active_x = obj.get("sdf_array_active_x", False); active_y = obj.get("sdf_array_active_y", False); active_z = obj.get("sdf_array_active_z", False)
                 array_func = None; args = None; array_type_str = "None"
                 if main_mode == 'LINEAR':
                    nx=max(1,obj.get("sdf_array_count_x",1)); ny=max(1,obj.get("sdf_array_count_y",1)); nz=max(1,obj.get("sdf_array_count_z",1))
                    dx=obj.get("sdf_array_delta_x",1.0); dy=obj.get("sdf_array_delta_y",1.0); dz=obj.get("sdf_array_delta_z",1.0)
                    if active_x and active_y and active_z and (nx>1 or ny>1 or nz>1): args = (current_shape,nx,ny,nz,(float(dx),float(dy),float(dz))); array_func=lf.array_xyz; array_type_str="LinXYZ"
                    elif active_x and active_y and (nx>1 or ny>1): args = (current_shape,nx,ny,(float(dx),float(dy))); array_func=lf.array_xy; array_type_str="LinXY"
                    elif active_x and nx>1: args = (current_shape,nx,float(dx)); array_func=lf.array_x; array_type_str="LinX"
                 elif main_mode == 'RADIAL':
                    count = max(1, obj.get("sdf_radial_count", 1)); center_prop = obj.get("sdf_radial_center", (0.0, 0.0))
                    if count > 1:
                        try: center_xy = (float(center_prop[0]), float(center_prop[1])) if center_prop and len(center_prop)==2 else (0.0, 0.0)
                        except: center_xy = (0.0, 0.0); print(f"FF WARN: Invalid radial center on {obj_name}")
                        args = (current_shape, count, center_xy); array_func = lf.array_polar_z; array_type_str = "Radial"
                 # Apply array
                 if array_func and args:
                    try:
                        # print(f"  DEBUG: Applying Array {array_type_str} to {obj_name}") # DEBUG
                        current_shape = array_func(*args)
                    except Exception as e: print(f"FieldForge ERROR (process_hierarchy): Arraying {obj_name} failed: {e}"); current_shape = lf.emptiness()

        # --- Stage 4: Apply Transform ---
        # Transform the final shape resulting from previous stages
        # Added checks for None and emptiness before transforming
        if current_shape is not None and current_shape is not lf.emptiness():
            try:
                # Use evaluated matrix for world transform
                obj_matrix_inv = obj.matrix_world.inverted()
                processed_shape_for_this_obj = apply_blender_transform_to_sdf(current_shape, obj_matrix_inv)
            except ValueError: # Matrix inversion failed
                print(f"FieldForge ERROR (process_hierarchy): Matrix inversion failed for {obj_name}. Cannot apply transform.")
                processed_shape_for_this_obj = lf.emptiness() # Error state
            except Exception as e:
                print(f"FieldForge ERROR (process_hierarchy): Transforming {obj_name} failed: {e}")
                processed_shape_for_this_obj = lf.emptiness()
        # else: current_shape is empty, processed_shape_for_this_obj remains empty

    # else: Object is not an SDF source, processed_shape_for_this_obj remains lf.emptiness()

    # --- Stage 5: Combine with Remaining (Non-Lofted, Visible) Children ---
    shape_so_far = processed_shape_for_this_obj # Start with this object's transformed shape
    # Make sure shape_so_far is not None before proceeding with children
    if shape_so_far is None: shape_so_far = lf.emptiness()

    # Determine blend factor for combining children TO this obj
    interaction_blend_factor = 0.0
    if is_bounds_root: # If obj is the Bounds Root
        interaction_blend_factor = settings.get("sdf_global_blend_factor", 0.1)
    elif utils.is_sdf_source(obj): # If obj is an SDF Source
        interaction_blend_factor = obj.get("sdf_child_blend_factor", 0.0)
    # Else (obj is just a regular Empty/grouping node), blend factor remains 0 (sharp union/difference)

    # Iterate through children
    for child in obj.children:
        # Skip children already handled by loft
        if child.name in processed_children:
            continue

        # Check child visibility and existence
        actual_child = bpy.context.scene.objects.get(child.name)
        if not actual_child or not actual_child.visible_get(view_layer=bpy.context.view_layer):
            continue # Skip non-existent or hidden children

        # print(f"  DEBUG: Processing Child: {child.name} of {obj_name}") # DEBUG
        child_processed_shape = process_sdf_hierarchy(child, settings) # Pass settings down

        # Combine using standard interaction modes if child shape is valid (not None AND not empty)
        if child_processed_shape is not None and child_processed_shape is not lf.emptiness():
            # Get interaction modes from the child object
            use_morph_on_child = child.get("sdf_use_morph", False)
            use_clearance_on_child = child.get("sdf_use_clearance", False)
            is_child_negative = child.get("sdf_is_negative", False)

            # Priority: Morph > Clearance > Negative > Additive
            try:
                if use_morph_on_child:
                    factor = child.get("sdf_morph_factor", 0.5); safe_factor = max(0.0, min(1.0, float(factor)))
                    shape_so_far = lf.morph(shape_so_far, child_processed_shape, safe_factor)
                elif use_clearance_on_child:
                    offset_val = child.get("sdf_clearance_offset", 0.05); keep_original = child.get("sdf_clearance_keep_original", True)
                    safe_offset = max(0.0, float(offset_val))
                    offset_child_shape = lf.offset(child_processed_shape, safe_offset)
                    shape_after_cut = lf.difference(shape_so_far, offset_child_shape)
                    if keep_original: # Combine cut result with original child shape
                        shape_so_far = combine_shapes(shape_after_cut, child_processed_shape, interaction_blend_factor)
                    else: # Only keep the cut result
                        shape_so_far = shape_after_cut
                elif is_child_negative: # Subtractive
                     safe_blend = max(0.0, interaction_blend_factor)
                     if safe_blend <= constants.CACHE_PRECISION: shape_so_far = lf.difference(shape_so_far, child_processed_shape)
                     else: clamped_blend = min(1.0, safe_blend); shape_so_far = lf.blend_difference(shape_so_far, child_processed_shape, clamped_blend)
                else: # Additive mode (Union or Blend)
                    shape_so_far = combine_shapes(shape_so_far, child_processed_shape, interaction_blend_factor)
            except Exception as e_combine:
                 print(f"FieldForge ERROR (process_hierarchy): Failed combining child {child.name} to {obj.name}: {e_combine}")
                 # Decide error strategy: skip child, return emptiness? Skipping child seems safer.
                 continue # Skip to next child on combination error
        # else: print(f"  DEBUG: Child {child.name} result was empty/None, skipping combination.") # DEBUG

    return shape_so_far