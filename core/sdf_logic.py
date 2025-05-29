"""
Core SDF Shape Construction Logic for FieldForge Addon.

Handles recursive processing of the Blender object hierarchy to build
a combined libfive.Shape based on object properties and transformations.
"""

import math
import bpy

try:
    import libfive.stdlib as lf
    import libfive.shape as libfive_shape_module
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
    libfive_shape_module = type('module', (), {'Shape': ShapeDummy})()


from mathutils import Vector, Matrix

from .. import constants
from .. import utils


def reconstruct_shape(obj) -> lf.Shape | None:
    """
    Reconstructs a UNIT libfive shape based on the object's 'sdf_type' property.
    Scaling and transformation are handled separately via the object's matrix.
    Extrusion for 2D shapes is handled later in process_sdf_hierarchy.

    Returns a libfive Shape or lf.emptiness() on error/unknown type.
    """
    if not _lf_imported_ok or not obj:
        return lf.emptiness() if _lf_imported_ok else None

    sdf_type = obj.get("sdf_type", "")
    shape = None
    unit_radius = 0.5 # Standard radius for shapes like cylinder/cone base/sphere/circle
    unit_height = 1.0 # Standard height for shapes like cylinder/cone
    half_size = 0.5 # Half-dimension for unit cube/box related calculations

    unit_pyramid_base_half_x = 0.5
    unit_pyramid_base_half_y = 0.5
    unit_pyramid_height = 1.0
    unit_pyramid_zmin = 0

    try:
        if sdf_type == "cube":
            shape = lf.cube_centered((2 * half_size, 2 * half_size, 2 * half_size))
        elif sdf_type == "sphere":
            shape = lf.sphere(unit_radius)
        elif sdf_type == "cylinder":
            shape = lf.cylinder_z(unit_radius, unit_height, base=(0, 0, -half_size))
        elif sdf_type == "cone":
            if unit_height <= 1e-6:
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
        elif sdf_type == "pyramid":
            base_corner_a = (-unit_pyramid_base_half_x, -unit_pyramid_base_half_y)
            base_corner_b = ( unit_pyramid_base_half_x,  unit_pyramid_base_half_y)
            shape = lf.pyramid_z(
                base_corner_a,
                base_corner_b,
                unit_pyramid_zmin,
                unit_pyramid_height
            )
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
        elif sdf_type == "text": # NEW SHAPE
            text_string = obj.get("sdf_text_string", constants.DEFAULT_SOURCE_SETTINGS["sdf_text_string"])
            if not text_string.strip(): # If string is empty or only whitespace
                print(f"FieldForge WARN (reconstruct_shape): Empty text string for {obj.name}. Returning empty shape.")
                return lf.emptiness()

            num_chars = len(text_string)
            # A very rough estimated width, assuming char height is 1 and aspect is ~0.7
            estimated_width = num_chars * 0.7 
            start_pos_x = -estimated_width / 2.0 
            # Y position: libfive text seems to draw along baseline, so to center vertically around y=0:
            start_pos_y = -0.5 # Assuming char height of 1, baseline starts slightly down
            
            shape = lf.text(text_string, (start_pos_x, start_pos_y))
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

    X, Y, Z = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
    mat_inv = obj_matrix_world_inv
    try:
        x_p = mat_inv[0][0] * X + mat_inv[0][1] * Y + mat_inv[0][2] * Z + mat_inv[0][3]
        y_p = mat_inv[1][0] * X + mat_inv[1][1] * Y + mat_inv[1][2] * Z + mat_inv[1][3]
        z_p = mat_inv[2][0] * X + mat_inv[2][1] * Y + mat_inv[2][2] * Z + mat_inv[2][3]
        transformed_shape = shape.remap(x_p, y_p, z_p)
    except OverflowError:
         print(f"FieldForge ERROR (apply_transform): OverflowError during remap for shape (obj likely has extreme transform values).")
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

def custom_blended_intersection(shape_a, shape_b, blend_factor_m, lf_module):
    if shape_a is lf_module.emptiness() or shape_b is lf_module.emptiness(): return lf_module.emptiness()
    try:
        inv_a = lf_module.inverse(shape_a)
        inv_b = lf_module.inverse(shape_b)
        smooth_union_of_inverses = lf_module.blend_expt_unit(inv_a, inv_b, blend_factor_m)
        return lf_module.inverse(smooth_union_of_inverses)
    except Exception as e:
        print(f"FieldForge ERROR (custom_blended_intersection): {e}. Falling back.")
        try: return lf_module.intersection(shape_a, shape_b)
        except: return lf_module.emptiness()

def process_sdf_hierarchy(obj: bpy.types.Object, bounds_settings: dict) -> lf.Shape | None:
    context = bpy.context
    if not obj.visible_get(view_layer=context.view_layer):
        return lf.emptiness()

    obj_name = obj.name
    obj_is_sdf_source = utils.is_sdf_source(obj)
    obj_is_group = utils.is_sdf_group(obj)
    current_scene_shape = lf.emptiness() if _lf_imported_ok else None

    if obj_is_sdf_source:
        unit_shape_modified_by_own_ops = reconstruct_shape(obj)
        
        sdf_type = obj.get("sdf_type", "")
        is_2d_shape_for_extrude = sdf_type in {"circle", "ring", "polygon", "text"}
        if is_2d_shape_for_extrude and (unit_shape_modified_by_own_ops is not None and unit_shape_modified_by_own_ops is not lf.emptiness()):
            depth = obj.get("sdf_extrusion_depth", constants.DEFAULT_SOURCE_SETTINGS["sdf_extrusion_depth"])
            if float(depth) > 1e-5:
                try: unit_shape_modified_by_own_ops = lf.extrude_z(unit_shape_modified_by_own_ops, 0, abs(float(depth)))
                except Exception as e: print(f"FieldForge ERROR (extrude_z for {obj_name}): {e}"); unit_shape_modified_by_own_ops = lf.emptiness()

        if obj.get("sdf_use_shell", False) and (unit_shape_modified_by_own_ops is not None and unit_shape_modified_by_own_ops is not lf.emptiness()):
            offset = float(obj.get("sdf_shell_offset", constants.DEFAULT_SOURCE_SETTINGS["sdf_shell_offset"]))
            if abs(offset) > 1e-5:
                try:
                    outer_surface = lf.offset(unit_shape_modified_by_own_ops, offset)
                    if offset > 0: unit_shape_modified_by_own_ops = lf.difference(outer_surface, unit_shape_modified_by_own_ops)
                    else: unit_shape_modified_by_own_ops = lf.difference(unit_shape_modified_by_own_ops, outer_surface)
                except Exception as e: print(f"FieldForge ERROR (shell for {obj_name}): {e}"); unit_shape_modified_by_own_ops = lf.emptiness()

        array_mode = obj.get("sdf_main_array_mode", 'NONE')
        center_on_origin = obj.get("sdf_array_center_on_origin", True)
        if array_mode != 'NONE' and (unit_shape_modified_by_own_ops is not None and unit_shape_modified_by_own_ops is not lf.emptiness()):
            current_shape_before_array = unit_shape_modified_by_own_ops
            if array_mode == 'LINEAR':
                active_x=obj.get("sdf_array_active_x",0); active_y=obj.get("sdf_array_active_y",0) and active_x; active_z=obj.get("sdf_array_active_z",0) and active_y
                nx=max(1,int(obj.get("sdf_array_count_x",2))) if active_x else 1
                ny=max(1,int(obj.get("sdf_array_count_y",2))) if active_y else 1
                nz=max(1,int(obj.get("sdf_array_count_z",2))) if active_z else 1
                dx_val=float(obj.get("sdf_array_delta_x",1)) if active_x else 0; dy_val=float(obj.get("sdf_array_delta_y",1)) if active_y else 0; dz_val=float(obj.get("sdf_array_delta_z",1)) if active_z else 0
                array_applied=False
                try:
                    if active_z:
                        if nx>1 or ny>1 or nz>1: unit_shape_modified_by_own_ops=lf.array_xyz(current_shape_before_array,nx,ny,nz,(dx_val,dy_val,dz_val)); array_applied=True
                    elif active_y:
                        if nx>1 or ny>1: unit_shape_modified_by_own_ops=lf.array_xy(current_shape_before_array,nx,ny,(dx_val,dy_val)); array_applied=True
                    elif active_x:
                        if nx>1: unit_shape_modified_by_own_ops=lf.array_x(current_shape_before_array,nx,dx_val); array_applied=True
                    if array_applied and center_on_origin:
                        total_offset_x=(nx-1)*dx_val if active_x and nx>1 else 0; total_offset_y=(ny-1)*dy_val if active_y and ny>1 else 0; total_offset_z=(nz-1)*dz_val if active_z and nz>1 else 0
                        center_shift_x=-total_offset_x/2.0; center_shift_y=-total_offset_y/2.0; center_shift_z=-total_offset_z/2.0
                        if abs(center_shift_x)>1e-6 or abs(center_shift_y)>1e-6 or abs(center_shift_z)>1e-6:
                            if (unit_shape_modified_by_own_ops is not None and unit_shape_modified_by_own_ops is not lf.emptiness()):
                                X,Y,Z=libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z()
                                unit_shape_modified_by_own_ops=unit_shape_modified_by_own_ops.remap(X-center_shift_x,Y-center_shift_y,Z-center_shift_z)
                except Exception as e: print(f"FieldForge ERROR (Linear Array for {obj_name}): {e}"); unit_shape_modified_by_own_ops=current_shape_before_array
            elif array_mode == 'RADIAL':
                count=max(1,int(obj.get("sdf_radial_count",1))); center_prop=obj.get("sdf_radial_center",(0.0,0.0))
                if count > 1:
                    try: center_xy_pivot=(float(center_prop[0]),float(center_prop[1]))
                    except: center_xy_pivot=(0.0,0.0); print(f"FieldForge WARN: Invalid radial center on {obj_name}, using (0,0).")
                    try:
                        unit_shape_modified_by_own_ops=lf.array_polar_z(current_shape_before_array,count,center_xy_pivot)
                        if center_on_origin and (abs(center_xy_pivot[0])>1e-6 or abs(center_xy_pivot[1])>1e-6):
                            if (unit_shape_modified_by_own_ops is not None and unit_shape_modified_by_own_ops is not lf.emptiness()):
                                X,Y,Z=libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z()
                                unit_shape_modified_by_own_ops=unit_shape_modified_by_own_ops.remap(X+center_xy_pivot[0],Y+center_xy_pivot[1],Z)
                    except Exception as e: print(f"FieldForge ERROR (Radial Array for {obj_name}): {e}"); unit_shape_modified_by_own_ops=current_shape_before_array

        if (unit_shape_modified_by_own_ops is not None and unit_shape_modified_by_own_ops is not lf.emptiness()):
            try:
                current_scene_shape = apply_blender_transform_to_sdf(unit_shape_modified_by_own_ops, obj.matrix_world.inverted())
            except Exception as e:
                print(f"FieldForge ERROR (transforming self for {obj_name}): {e}")
                current_scene_shape = lf.emptiness() if _lf_imported_ok else None
    
    parent_provides_blend_factor = 0.0
    if obj_is_sdf_source:
        parent_provides_blend_factor = float(obj.get("sdf_child_blend_factor", constants.DEFAULT_SOURCE_SETTINGS["sdf_child_blend_factor"]))
    elif obj_is_group:
        parent_provides_blend_factor = float(obj.get("sdf_child_blend_factor", constants.DEFAULT_GROUP_SETTINGS["sdf_child_blend_factor"]))

    children_to_process = []
    for child_candidate in obj.children:
        if child_candidate and child_candidate.visible_get(view_layer=context.view_layer):
            if utils.is_sdf_source(child_candidate) or \
               utils.is_sdf_group(child_candidate) or \
               (child_candidate.type == 'EMPTY' and not child_candidate.get(constants.SDF_BOUNDS_MARKER)):
                children_to_process.append(child_candidate)

    def get_sort_key_for_processing(child_obj_param):
        order = child_obj_param.get("sdf_processing_order", float('inf'))
        return (order, child_obj_param.name)
    sorted_children = sorted(children_to_process, key=get_sort_key_for_processing)

    for child in sorted_children:

        child_name = child.name
        is_current_obj_valid_loft_base = obj_is_sdf_source and \
                                         obj.get("sdf_use_loft", False) and \
                                         utils.is_valid_2d_loft_source(obj)
        is_child_valid_loft_target = utils.is_sdf_source(child) and \
                                     child.get("sdf_use_loft", False) and \
                                     utils.is_valid_2d_loft_source(child)

        if is_current_obj_valid_loft_base and is_child_valid_loft_target:
            parent_2d_unit_profile_for_loft = reconstruct_shape(obj)
            child_2d_unit_profile_for_loft = reconstruct_shape(child)
            lofted_contribution_world = lf.emptiness() if _lf_imported_ok else None
            if (parent_2d_unit_profile_for_loft is None or parent_2d_unit_profile_for_loft is lf.emptiness()) or \
               (child_2d_unit_profile_for_loft is None or child_2d_unit_profile_for_loft is lf.emptiness()):
                print(f"FieldForge WARN (loft): Invalid 2D unit profiles for loft between {obj_name} and {child_name}.")
            else:
                try:
                    child_matrix_relative_to_parent = obj.matrix_world.inverted() @ child.matrix_world
                    height = child_matrix_relative_to_parent.translation.z
                    child_scale_vec_relative = child_matrix_relative_to_parent.to_scale()
                    relative_profile_scale = (abs(child_scale_vec_relative.x) + abs(child_scale_vec_relative.y)) / 2.0
                    relative_profile_scale = max(1e-3, relative_profile_scale)
                    scaled_child_2d_profile = child_2d_unit_profile_for_loft
                    if abs(relative_profile_scale - 1.0) > 1e-5:
                        scaled_child_2d_profile = lf.scale(child_2d_unit_profile_for_loft, (relative_profile_scale, relative_profile_scale, 1.0))
                    lofted_shape_local_to_obj = lf.loft(parent_2d_unit_profile_for_loft, scaled_child_2d_profile, 0, height)
                    if (lofted_shape_local_to_obj is not None and lofted_shape_local_to_obj is not lf.emptiness()):
                        lofted_contribution_world = apply_blender_transform_to_sdf(lofted_shape_local_to_obj, obj.matrix_world.inverted())
                except Exception as e:
                    print(f"FieldForge ERROR (lofting {obj_name} to {child_name}): {e}")
            current_scene_shape = combine_shapes(current_scene_shape, lofted_contribution_world, parent_provides_blend_factor)
            continue

        processed_child_subtree_world = process_sdf_hierarchy(child, bounds_settings)
        if (processed_child_subtree_world is None or processed_child_subtree_world is lf.emptiness()):
            continue

        if utils.is_sdf_source(child):
            use_morph = child.get("sdf_use_morph", False)
            use_clearance = child.get("sdf_use_clearance", False) and not use_morph
            child_csg_op_type = child.get("sdf_csg_operation", constants.DEFAULT_SOURCE_SETTINGS["sdf_csg_operation"])

            if use_morph:
                morph_factor = float(child.get("sdf_morph_factor", 0.5))
                try: current_scene_shape = lf.blend_expt_unit(processed_child_subtree_world, current_scene_shape, morph_factor)
                except Exception as e: print(f"FieldForge ERROR (morphing {obj_name} with {child_name}): {e}")
            elif use_clearance:
                offset_val = float(child.get("sdf_clearance_offset", 0.05))
                keep_original = child.get("sdf_clearance_keep_original", True)
                try:
                    offset_child_shape_for_subtraction = lf.offset(processed_child_subtree_world, offset_val)
                    current_scene_shape = lf.difference(current_scene_shape, offset_child_shape_for_subtraction)
                    if keep_original: 
                        current_scene_shape = combine_shapes(current_scene_shape, processed_child_subtree_world, parent_provides_blend_factor)
                except Exception as e: print(f"FieldForge ERROR (clearance {child_name} on {obj_name}): {e}")
            elif child_csg_op_type == "NONE": pass
            elif child_csg_op_type == "UNION":
                current_scene_shape = combine_shapes(current_scene_shape, processed_child_subtree_world, parent_provides_blend_factor)
            elif child_csg_op_type == "INTERSECT":
                try:
                    blend_radius_for_intersect = parent_provides_blend_factor
                    if blend_radius_for_intersect > constants.CACHE_PRECISION:
                        clamped_blend_intersect = min(max(0.0, blend_radius_for_intersect), 1.0)
                        current_scene_shape = custom_blended_intersection(current_scene_shape, processed_child_subtree_world, clamped_blend_intersect, lf)
                    else:
                        current_scene_shape = lf.intersection(current_scene_shape, processed_child_subtree_world)
                except Exception as e: print(f"FieldForge ERROR (intersecting {child_name} with {obj_name}): {e}"); current_scene_shape = lf.emptiness() if _lf_imported_ok else None
            elif child_csg_op_type == "DIFFERENCE":
                if (current_scene_shape is None or current_scene_shape is lf.emptiness()) or \
                   (processed_child_subtree_world is None or processed_child_subtree_world is lf.emptiness()):
                    pass 
                else:
                    try:
                        blend_radius_for_difference = parent_provides_blend_factor
                        if blend_radius_for_difference > constants.CACHE_PRECISION:
                            clamped_blend_difference = min(max(0.0, blend_radius_for_difference), 1.0) 
                            current_scene_shape = lf.blend_difference(current_scene_shape, processed_child_subtree_world, clamped_blend_difference)
                        else:
                            current_scene_shape = lf.difference(current_scene_shape, processed_child_subtree_world)
                    except Exception as e: 
                        print(f"FieldForge ERROR (subtracting {child_name} from {obj_name}): {e}")
                        try: current_scene_shape = lf.difference(current_scene_shape, processed_child_subtree_world)
                        except: pass 
            else: 
                print(f"FieldForge WARN: Unknown sdf_csg_operation '{child_csg_op_type}' for source {child_name}. Defaulting to union.")
                current_scene_shape = combine_shapes(current_scene_shape, processed_child_subtree_world, parent_provides_blend_factor)
        elif utils.is_sdf_group(child) or (child.type == 'EMPTY' and not child.get(constants.SDF_BOUNDS_MARKER)):
            current_scene_shape = combine_shapes(current_scene_shape, processed_child_subtree_world, parent_provides_blend_factor)
    if current_scene_shape is None and _lf_imported_ok:
        return lf.emptiness()
    return current_scene_shape