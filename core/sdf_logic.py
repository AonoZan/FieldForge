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
            if name == "emptiness":
                return lambda: None
            raise RuntimeError(f"libfive not available (tried to access lf.{name})")
    lf = LFDummy()
    class ShapeDummy:
        @staticmethod
        def X(): raise RuntimeError("libfive not available (Shape.X)")
        @staticmethod
        def Y(): raise RuntimeError("libfive not available (Shape.Y)")
        @staticmethod
        def Z(): raise RuntimeError("libfive not available (Shape.Z)")
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
            major_r = max(0.01, float(major_r_prop)); minor_r = max(0.005, float(minor_r_prop))
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
            safe_inner_r = max(0.0, min(float(inner_r_prop), unit_radius - 1e-5))
            shape = lf.ring(unit_radius, safe_inner_r, center=(0, 0))
        elif sdf_type == "polygon":
            sides = obj.get("sdf_sides", constants.DEFAULT_SOURCE_SETTINGS["sdf_sides"])
            safe_n = max(3, int(sides))
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
    if shape is None or shape is lf.emptiness(): # Cannot transform None
    # Check if shape is already emptiness to avoid unnecessary work
        return lf.emptiness()
    if obj_matrix_world_inv is None:
        print(f"FieldForge WARN (apply_transform): Received None matrix_world_inv.")
        return lf.emptiness()

    X, Y, Z = libfive.shape.Shape.X(), libfive.shape.Shape.Y(), libfive.shape.Shape.Z()
    mat_inv = obj_matrix_world_inv
    try:
        x_p = mat_inv[0][0] * X + mat_inv[0][1] * Y + mat_inv[0][2] * Z + mat_inv[0][3]
        y_p = mat_inv[1][0] * X + mat_inv[1][1] * Y + mat_inv[1][2] * Z + mat_inv[1][3]
        z_p = mat_inv[2][0] * X + mat_inv[2][1] * Y + mat_inv[2][2] * Z + mat_inv[2][3]
        transformed_shape = shape.remap(x_p, y_p, z_p)
    except OverflowError:
         print(f"FieldForge ERROR (apply_transform): OverflowError during remap for '{shape}' (likely extreme transform values).")
         return lf.emptiness()
    except TypeError as e:
        print(f"FieldForge ERROR (apply_transform): TypeError during remap: {e}")
        return lf.emptiness()
    except Exception as e:
        print(f"FieldForge ERROR (apply_transform): Unexpected error during libfive remap: {type(e).__name__} - {e}")
        return lf.emptiness()
    return transformed_shape

def combine_shapes(shape_a, shape_b, blend_factor) -> lf.Shape | None:
    if not _lf_imported_ok: return None
    is_a_empty = shape_a is None or shape_a is lf.emptiness()
    is_b_empty = shape_b is None or shape_b is lf.emptiness()

    if is_a_empty and is_b_empty: return lf.emptiness()
    if is_a_empty: return shape_b
    if is_b_empty: return shape_a

    try:
        safe_blend = max(0.0, float(blend_factor))
        if safe_blend > constants.CACHE_PRECISION:
            clamped_factor = min(max(0.0, safe_blend), 5.0)
            return lf.blend_expt_unit(shape_a, shape_b, clamped_factor)
        else:
            return lf.union(shape_a, shape_b)
    except Exception as e:
        print(f"FieldForge ERROR (combine_shapes): Error combining shapes: {e}")
        return lf.emptiness()

def process_sdf_hierarchy(obj: bpy.types.Object, settings: dict) -> lf.Shape | None:
    if not _lf_imported_ok:
        return lf.emptiness() # type: ignore

    context = bpy.context
    if not obj.visible_get(view_layer=context.view_layer):
        return lf.emptiness()

    obj_name = obj.name # For logging

    # --- 1. Determine object's own non-lofted contribution (if it's a source) ---
    obj_own_initial_shape_world = lf.emptiness()
    if utils.is_sdf_source(obj):
        unit_shape_modified_by_own_ops = reconstruct_shape(obj)
        
        sdf_type = obj.get("sdf_type", "")
        is_2d_shape_for_extrude = sdf_type in {"circle", "ring", "polygon"}
        if is_2d_shape_for_extrude and unit_shape_modified_by_own_ops is not lf.emptiness():
            depth = obj.get("sdf_extrusion_depth", constants.DEFAULT_SOURCE_SETTINGS["sdf_extrusion_depth"])
            if float(depth) > 1e-5:
                try: unit_shape_modified_by_own_ops = lf.extrude_z(unit_shape_modified_by_own_ops, 0, abs(float(depth)))
                except Exception as e: print(f"FF ERROR (extrude_z for {obj_name}): {e}"); unit_shape_modified_by_own_ops = lf.emptiness()
        
        if obj.get("sdf_use_shell", False) and unit_shape_modified_by_own_ops is not lf.emptiness():
            offset = float(obj.get("sdf_shell_offset", constants.DEFAULT_SOURCE_SETTINGS["sdf_shell_offset"]))
            if abs(offset) > 1e-5:
                try:
                    outer_surface = lf.offset(unit_shape_modified_by_own_ops, offset)
                    if offset > 0: unit_shape_modified_by_own_ops = lf.difference(outer_surface, unit_shape_modified_by_own_ops)
                    else: unit_shape_modified_by_own_ops = lf.difference(unit_shape_modified_by_own_ops, outer_surface)
                except Exception as e: print(f"FF ERROR (shell for {obj_name}): {e}"); unit_shape_modified_by_own_ops = lf.emptiness()

        # --- Libfive Array Application ---
        array_mode = obj.get("sdf_main_array_mode", 'NONE')
        if array_mode != 'NONE' and unit_shape_modified_by_own_ops is not lf.emptiness():
            # temp_shape_for_modifiers in the snippet corresponds to unit_shape_modified_by_own_ops here
            current_shape_before_array = unit_shape_modified_by_own_ops # Store for potential error case

            active_x = obj.get("sdf_array_active_x", False)
            active_y = obj.get("sdf_array_active_y", False)
            active_z = obj.get("sdf_array_active_z", False)
            array_func = None; args = None; array_type_str = "None"

            if array_mode == 'LINEAR':
                nx=max(1,obj.get("sdf_array_count_x",1))
                ny=max(1,obj.get("sdf_array_count_y",1))
                nz=max(1,obj.get("sdf_array_count_z",1))
                dx=obj.get("sdf_array_delta_x",1.0)
                dy=obj.get("sdf_array_delta_y",1.0)
                dz=obj.get("sdf_array_delta_z",1.0)

                # Determine which array function to use based on active axes
                if active_x and active_y and active_z and (nx>1 or ny>1 or nz>1): 
                    args = (unit_shape_modified_by_own_ops,nx,ny,nz,(float(dx),float(dy),float(dz)))
                    array_func=lf.array_xyz; array_type_str="LinXYZ"
                elif active_x and active_y and (nx>1 or ny>1): 
                    args = (unit_shape_modified_by_own_ops,nx,ny,(float(dx),float(dy)))
                    array_func=lf.array_xy; array_type_str="LinXY"
                elif active_x and nx>1: 
                    args = (unit_shape_modified_by_own_ops,nx,float(dx))
                    array_func=lf.array_x; array_type_str="LinX"
            elif array_mode == 'RADIAL':
                count = max(1, obj.get("sdf_radial_count", 1))
                center_prop = obj.get("sdf_radial_center", (0.0, 0.0))
                if count > 1:
                    try: center_xy = (float(center_prop[0]), float(center_prop[1]))
                    except (TypeError, IndexError, ValueError): 
                        center_xy = (0.0, 0.0)
                        print(f"FF WARN: Invalid radial center on {obj_name}, using (0,0).")
                    args = (unit_shape_modified_by_own_ops, count, center_xy)
                    array_func = lf.array_polar_z
                    array_type_str = "Radial"
            
            if array_func and args:
                try:
                    # print(f"  DEBUG: Applying Array {array_type_str} to {obj_name}")
                    unit_shape_modified_by_own_ops = array_func(*args)
                except Exception as e: 
                    print(f"FieldForge ERROR (process_hierarchy): Arraying {obj_name} ({array_type_str}) failed: {e}")
                    unit_shape_modified_by_own_ops = current_shape_before_array # Revert to pre-array shape on error
        
        if unit_shape_modified_by_own_ops is not lf.emptiness():
            try:
                obj_own_initial_shape_world = apply_blender_transform_to_sdf(unit_shape_modified_by_own_ops, obj.matrix_world.inverted())
            except Exception as e:
                print(f"FieldForge ERROR (transforming self for {obj_name}): {e}")
                obj_own_initial_shape_world = lf.emptiness()
    
    current_scene_shape = obj_own_initial_shape_world

    is_root_bounds = obj.get(constants.SDF_BOUNDS_MARKER, False)
    obj_s_child_blend_factor = float(settings.get("sdf_global_blend_factor", 0.1)) if is_root_bounds \
                                else float(obj.get("sdf_child_blend_factor", 0.0))
    
    sorted_children = sorted(list(obj.children), key=lambda c: c.name)

    for child in sorted_children:
        if not child.visible_get(view_layer=context.view_layer):
            continue

        is_obj_loft_participant = utils.is_sdf_source(obj) and \
                                  obj.get("sdf_use_loft", False) and \
                                  utils.is_valid_2d_loft_source(obj)
        is_child_loft_participant = utils.is_sdf_source(child) and \
                                    child.get("sdf_use_loft", False) and \
                                    utils.is_valid_2d_loft_source(child)

        if is_obj_loft_participant and is_child_loft_participant:
            parent_2d_unit_profile_for_loft = reconstruct_shape(obj)
            child_2d_unit_profile_for_loft = reconstruct_shape(child)
            lofted_contribution_world = lf.emptiness()
            if parent_2d_unit_profile_for_loft is lf.emptiness() or child_2d_unit_profile_for_loft is lf.emptiness():
                print(f"FF WARN (loft): Invalid 2D unit profiles for loft between {obj_name} and {child.name}.")
            else:
                try:
                    child_matrix_relative_to_parent = obj.matrix_world.inverted() @ child.matrix_world
                    height = child_matrix_relative_to_parent.translation.z
                    child_scale_vec_relative = child_matrix_relative_to_parent.to_scale()
                    relative_profile_scale = (abs(child_scale_vec_relative.x) + abs(child_scale_vec_relative.y)) / 2.0
                    relative_profile_scale = max(1e-3, relative_profile_scale)
                    scaled_child_2d_profile = child_2d_unit_profile_for_loft
                    if abs(relative_profile_scale - 1.0) > 1e-5:
                        scaled_child_2d_profile = lf.scale(child_2d_unit_profile_for_loft,
                                                           (relative_profile_scale, relative_profile_scale, 1.0))
                    lofted_shape_local_to_obj = lf.loft(parent_2d_unit_profile_for_loft,
                                                        scaled_child_2d_profile,
                                                        0, height)
                    if lofted_shape_local_to_obj is not lf.emptiness():
                        lofted_contribution_world = apply_blender_transform_to_sdf(lofted_shape_local_to_obj, obj.matrix_world.inverted())
                except Exception as e:
                    print(f"FieldForge ERROR (lofting {obj_name} to {child.name}): {e}")
            current_scene_shape = combine_shapes(current_scene_shape, lofted_contribution_world, obj_s_child_blend_factor)
            continue

        processed_child_subtree_world = process_sdf_hierarchy(child, settings)
        if processed_child_subtree_world is None or processed_child_subtree_world is lf.emptiness():
            continue

        use_morph = child.get("sdf_use_morph", False)
        use_clearance = child.get("sdf_use_clearance", False) and not use_morph
        is_negative = child.get("sdf_is_negative", False) and not use_morph and not use_clearance

        if use_morph:
            morph_factor = float(child.get("sdf_morph_factor", 0.5))
            try: current_scene_shape = lf.blend_expt_unit(processed_child_subtree_world, current_scene_shape, morph_factor)
            except Exception as e: print(f"FF ERROR (morphing {obj_name} with {child.name}): {e}")
        elif use_clearance:
            offset_val = float(child.get("sdf_clearance_offset", 0.05))
            keep_original = child.get("sdf_clearance_keep_original", True)
            try:
                offset_child_shape = lf.offset(processed_child_subtree_world, offset_val)
                current_scene_shape = lf.difference(current_scene_shape, offset_child_shape)
                if keep_original:
                    current_scene_shape = combine_shapes(current_scene_shape, processed_child_subtree_world, obj_s_child_blend_factor)
            except Exception as e: print(f"FF ERROR (clearance {child.name} on {obj_name}): {e}")
        elif is_negative: # Subtractive
            safe_blend = max(0.0, obj_s_child_blend_factor)
            if safe_blend <= constants.CACHE_PRECISION: current_scene_shape = lf.difference(current_scene_shape, processed_child_subtree_world)
            else: clamped_blend = min(1.0, safe_blend); current_scene_shape = lf.blend_difference(current_scene_shape, processed_child_subtree_world, clamped_blend)
        else:
            current_scene_shape = combine_shapes(current_scene_shape, processed_child_subtree_world, obj_s_child_blend_factor)

    if current_scene_shape is None: return lf.emptiness()
    return current_scene_shape