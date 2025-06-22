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


def reconstruct_shape(obj: bpy.types.Object) -> lf.Shape | None:
    """
    Reconstructs a UNIT libfive shape based on the object's 'sdf_type' property.
    Scaling and transformation are handled separately via the object's matrix.
    Extrusion for 2D shapes is handled later in process_sdf_hierarchy.

    Returns a libfive Shape or lf.emptiness() on error/unknown type.
    """
    if not _lf_imported_ok or not obj:
        return lf.emptiness() if _lf_imported_ok else None

    sdf_type = utils.get_sdf_param(obj, "sdf_type", "")
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
            major_r_prop = utils.get_sdf_param(obj, "sdf_torus_major_radius", default_major)
            minor_r_prop = utils.get_sdf_param(obj, "sdf_torus_minor_radius", default_minor)
            major_r = max(0.01, float(major_r_prop)); minor_r = max(0.005, float(minor_r_prop))
            minor_r = min(minor_r, major_r - 1e-5)
            shape = lf.torus_z(major_r, minor_r, center=(0,0,0))

        elif sdf_type == "rounded_box":
            roundness_prop = utils.get_sdf_param(obj, "sdf_round_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_round_radius"])
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
            inner_r_prop = utils.get_sdf_param(obj, "sdf_inner_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_inner_radius"])
            # Ensure inner radius is relative to the unit_radius (0.5)
            safe_inner_r = max(0.0, min(float(inner_r_prop), unit_radius - 1e-5))
            shape = lf.ring(unit_radius, safe_inner_r, center=(0, 0))
        elif sdf_type == "polygon":
            sides = utils.get_sdf_param(obj, "sdf_sides", constants.DEFAULT_SOURCE_SETTINGS["sdf_sides"])
            safe_n = max(3, int(sides))
            shape = lf.polygon(unit_radius, safe_n, center=(0, 0))
        elif sdf_type == "text": # NEW SHAPE
            text_string = utils.get_sdf_param(obj, "sdf_text_string", constants.DEFAULT_SOURCE_SETTINGS["sdf_text_string"])
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


def apply_blender_transform_to_sdf(shape: lf.Shape, obj_matrix_world_inv: Matrix) -> lf.Shape | None:
    """
    Applies Blender object's inverted world transform to a libfive shape using remap.
    Returns lf.emptiness() on error.
    """
    if not _lf_imported_ok: return None
    if shape is None or shape is lf.emptiness():
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
        return shape.remap(x_p, y_p, z_p)
    except Exception: return lf.emptiness()

def combine_shapes(shape_a: lf.Shape, shape_b: lf.Shape, blend_factor: float) -> lf.Shape | None:
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

def custom_blended_intersection(shape_a: lf.Shape, shape_b: lf.Shape, blend_factor_m: float, lf_module) -> lf.Shape | None:
    if (shape_a is None or shape_a is lf_module.emptiness()) or \
       (shape_b is None or shape_b is lf_module.emptiness()):
       return lf_module.emptiness()
    try:
        inv_a = lf_module.inverse(shape_a)
        inv_b = lf_module.inverse(shape_b)
        smooth_union_of_inverses = lf_module.blend_expt_unit(inv_a, inv_b, blend_factor_m)
        return lf_module.inverse(smooth_union_of_inverses)
    except Exception as e:
        print(f"FieldForge ERROR (custom_blended_intersection): {e}. Falling back.")
        try: return lf_module.intersection(shape_a, shape_b)
        except: return lf_module.emptiness()

def blended_symmetric_x(shape_in: lf.Shape, blend_factor: float) -> lf.Shape | None:
    """
    Makes a shape reflection and then blends it with original based on blend factor.
    """
    if not _lf_imported_ok or shape_in is None or shape_in is lf.emptiness(): return shape_in
    try: return lf.blend_expt_unit(shape_in, lf.reflect_x(shape_in), blend_factor)
    except Exception: return shape_in

def blended_symmetric_y(shape_in: lf.Shape, blend_factor: float) -> lf.Shape | None:
    if not _lf_imported_ok or shape_in is None or shape_in is lf.emptiness(): return shape_in
    try: return lf.blend_expt_unit(shape_in, lf.reflect_y(shape_in), blend_factor)
    except Exception: return shape_in

def blended_symmetric_z(shape_in: lf.Shape, blend_factor: float) -> lf.Shape | None:
    if not _lf_imported_ok or shape_in is None or shape_in is lf.emptiness(): return shape_in
    try: return lf.blend_expt_unit(shape_in, lf.reflect_z(shape_in), blend_factor)
    except Exception: return shape_in


def _apply_array_to_shape(
    shape_to_array_world: lf.Shape, 
    array_controller_obj: bpy.types.Object, 
    child_obj_for_logging: bpy.types.Object,
    delta_override: tuple | None = None
    ) -> lf.Shape | None:
    if not _lf_imported_ok or shape_to_array_world is None or shape_to_array_world is lf.emptiness():
        return shape_to_array_world

    array_mode = utils.get_sdf_param(array_controller_obj, "sdf_main_array_mode", 'NONE')
    if array_mode == 'NONE': return shape_to_array_world

    shape_in_controller_local_space = lf.emptiness()
    try:
        mat_l2w_controller = array_controller_obj.matrix_world
        X_r, Y_r, Z_r = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
        xp_r = mat_l2w_controller[0][0]*X_r + mat_l2w_controller[0][1]*Y_r + mat_l2w_controller[0][2]*Z_r + mat_l2w_controller[0][3]
        yp_r = mat_l2w_controller[1][0]*X_r + mat_l2w_controller[1][1]*Y_r + mat_l2w_controller[1][2]*Z_r + mat_l2w_controller[1][3]
        zp_r = mat_l2w_controller[2][0]*X_r + mat_l2w_controller[2][1]*Y_r + mat_l2w_controller[2][2]*Z_r + mat_l2w_controller[2][3]
        shape_in_controller_local_space = shape_to_array_world.remap(xp_r, yp_r, zp_r)
    except Exception: return shape_to_array_world 

    if shape_in_controller_local_space is None or shape_in_controller_local_space is lf.emptiness():
        return shape_to_array_world

    arrayed_shape_local = shape_in_controller_local_space 
    center_on_origin = utils.get_sdf_param(array_controller_obj, "sdf_array_center_on_origin", True)

    if array_mode == 'LINEAR':
        ax=utils.get_sdf_param(array_controller_obj,"sdf_array_active_x",False); ay=utils.get_sdf_param(array_controller_obj,"sdf_array_active_y",False) and ax; az=utils.get_sdf_param(array_controller_obj,"sdf_array_active_z",False) and ay
        nx=max(1,int(utils.get_sdf_param(array_controller_obj,"sdf_array_count_x",2))) if ax else 1; ny=max(1,int(utils.get_sdf_param(array_controller_obj,"sdf_array_count_y",2))) if ay else 1; nz=max(1,int(utils.get_sdf_param(array_controller_obj,"sdf_array_count_z",2))) if az else 1
        dx, dy, dz = delta_override
        applied=False
        try:
            if az: 
                if nx>1 or ny>1 or nz>1: arrayed_shape_local=lf.array_xyz(shape_in_controller_local_space,nx,ny,nz,(dx,dy,dz)); applied=True
            elif ay:
                if nx>1 or ny>1: arrayed_shape_local=lf.array_xy(shape_in_controller_local_space,nx,ny,(dx,dy)); applied=True
            elif ax:
                if nx>1: arrayed_shape_local=lf.array_x(shape_in_controller_local_space,nx,dx); applied=True
            if applied:
                center_shift_x = -(dx*(nx-1))
                center_shift_y = -(dy*(ny-1))
                center_shift_z = -(dz*(nz-1))

                if abs(center_shift_x)>1e-6 or abs(center_shift_y)>1e-6 or abs(center_shift_z)>1e-6:
                    X_arr_c,Y_arr_c,Z_arr_c=libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z()
                    arrayed_shape_local=arrayed_shape_local.remap(X_arr_c-center_shift_x,Y_arr_c-center_shift_y,Z_arr_c-center_shift_z)
        except Exception: arrayed_shape_local = shape_in_controller_local_space
    elif array_mode == 'RADIAL':
        count_rad=max(1,int(utils.get_sdf_param(array_controller_obj,"sdf_radial_count",1))); center_prop_rad=utils.get_sdf_param(array_controller_obj,"sdf_radial_center",(0.0,0.0))
        if count_rad > 1:
            try: pivot_rad=(float(center_prop_rad[0]),float(center_prop_rad[1]))
            except: pivot_rad=(0.0,0.0)
            try:
                arrayed_shape_local=lf.array_polar_z(shape_in_controller_local_space,count_rad,pivot_rad)
                if center_on_origin and (abs(pivot_rad[0])>1e-6 or abs(pivot_rad[1])>1e-6):
                    X_rs,Y_rs,Z_rs=libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z()
                    arrayed_shape_local=arrayed_shape_local.remap(X_rs+pivot_rad[0],Y_rs+pivot_rad[1],Z_rs)
            except Exception: arrayed_shape_local = shape_in_controller_local_space

    return apply_blender_transform_to_sdf(arrayed_shape_local, array_controller_obj.matrix_world.inverted())


def _process_children_recursive(
    children_owner_obj: bpy.types.Object, 
    current_logical_parent_obj: bpy.types.Object, 
    shape_accumulator: lf.Shape,
    bounds_settings: dict,
    context: bpy.types.Context,
    is_processing_as_linked_child_instance: bool 
    ) -> lf.Shape | None:
    if not _lf_imported_ok: return lf.emptiness() if _lf_imported_ok else None

    children_to_process_list = []
    is_children_owner_canvas = utils.is_sdf_canvas(children_owner_obj)
    for child_candidate in children_owner_obj.children:
        if child_candidate and child_candidate.visible_get(view_layer=context.view_layer):
            if is_children_owner_canvas and utils.is_sdf_source(child_candidate) and \
               utils.get_sdf_param(child_candidate, "sdf_type", "") in constants._2D_SHAPE_TYPES:
                continue 
            if utils.is_sdf_source(child_candidate) or utils.is_sdf_group(child_candidate) or utils.is_sdf_canvas(child_candidate):
                children_to_process_list.append(child_candidate)
    
    sorted_children_list = sorted(children_to_process_list, key=lambda c: (utils.get_sdf_param(c, "sdf_processing_order", float('inf')), c.name))

    parent_array_mode = utils.get_sdf_param(current_logical_parent_obj, "sdf_main_array_mode", 'NONE')
    is_logical_parent_an_arraying_group = utils.is_sdf_group(current_logical_parent_obj) and parent_array_mode != 'NONE'

    for child_in_list in sorted_children_list:
        child_name = child_in_list.name 

        can_owner_be_loft_base = utils.is_sdf_source(children_owner_obj) and utils.get_sdf_param(children_owner_obj, "sdf_use_loft", False) and utils.is_valid_2d_loft_source(children_owner_obj)
        can_child_be_loft_target = utils.is_sdf_source(child_in_list) and utils.get_sdf_param(child_in_list, "sdf_use_loft", False) and utils.is_valid_2d_loft_source(child_in_list)

        if can_owner_be_loft_base and can_child_be_loft_target:
            base_profile_unit = reconstruct_shape(children_owner_obj)
            target_profile_unit = reconstruct_shape(child_in_list)
            lofted_world = lf.emptiness() if _lf_imported_ok else None
            if not (base_profile_unit is None or base_profile_unit is lf.emptiness() or target_profile_unit is None or target_profile_unit is lf.emptiness()):
                try:
                    mat_child_rel_to_owner = children_owner_obj.matrix_world.inverted() @ child_in_list.matrix_world
                    loft_height = mat_child_rel_to_owner.translation.z
                    scale_vec_rel = mat_child_rel_to_owner.to_scale()
                    profile_scale_factor = max(1e-3, (abs(scale_vec_rel.x) + abs(scale_vec_rel.y)) / 2.0)
                    scaled_target_profile = target_profile_unit
                    if abs(profile_scale_factor - 1.0) > 1e-5:
                        try: scaled_target_profile = lf.scale_xy(target_profile_unit, (profile_scale_factor, profile_scale_factor))
                        except AttributeError: scaled_target_profile = lf.scale(target_profile_unit, (profile_scale_factor, profile_scale_factor, 1.0))
                    lofted_local_to_owner = lf.loft(base_profile_unit, scaled_target_profile, 0, loft_height)
                    if not (lofted_local_to_owner is None or lofted_local_to_owner is lf.emptiness()):
                        lofted_world = apply_blender_transform_to_sdf(lofted_local_to_owner, children_owner_obj.matrix_world.inverted())
                except Exception: pass 
            
            final_child_contribution_world = lofted_world
            if not (final_child_contribution_world is None or final_child_contribution_world is lf.emptiness()):
                child_blend_factor = float(utils.get_sdf_param(child_in_list, "sdf_blend_factor", 0.0))
                shape_accumulator = combine_shapes(shape_accumulator, final_child_contribution_world, child_blend_factor)
            continue

        child_subtree_contribution_world: lf.Shape | None

        if is_processing_as_linked_child_instance:
            child_original_full_world_shape = process_sdf_hierarchy(child_in_list, bounds_settings)

            if not (child_original_full_world_shape is None or child_original_full_world_shape is lf.emptiness()):

                mat_A_world = current_logical_parent_obj.matrix_world
                mat_B_world = children_owner_obj.matrix_world
            
                transform_for_reparenting_inv = mat_B_world @ mat_A_world.inverted()
                
                child_subtree_contribution_world = apply_blender_transform_to_sdf(
                    child_original_full_world_shape,
                    transform_for_reparenting_inv
                )
            else:
                child_subtree_contribution_world = lf.emptiness()
        else: 
            child_subtree_contribution_world = process_sdf_hierarchy(child_in_list, bounds_settings)

        if child_subtree_contribution_world is None or child_subtree_contribution_world is lf.emptiness():
            continue

        final_child_contribution_world = child_subtree_contribution_world
        if is_logical_parent_an_arraying_group:
            delta_override = None
            if parent_array_mode == 'LINEAR':
                active_x = utils.get_sdf_param(current_logical_parent_obj, "sdf_array_active_x", False)
                active_y = utils.get_sdf_param(current_logical_parent_obj, "sdf_array_active_y", False) and active_x
                active_z = utils.get_sdf_param(current_logical_parent_obj, "sdf_array_active_z", False) and active_y

                child_local_pos_in_obj = (children_owner_obj.matrix_world.inverted() @ child_in_list.matrix_world).translation

                nx = max(1, int(utils.get_sdf_param(current_logical_parent_obj, "sdf_array_count_x", 2))) if active_x else 1
                ny = max(1, int(utils.get_sdf_param(current_logical_parent_obj, "sdf_array_count_y", 2))) if active_y else 1
                nz = max(1, int(utils.get_sdf_param(current_logical_parent_obj, "sdf_array_count_z", 2))) if active_z else 1

                dx_val = (child_local_pos_in_obj.x * 2.0/ (nx-1)) if (active_x and nx > 1) else 0.0
                dy_val = (child_local_pos_in_obj.y * 2.0/ (ny-1)) if (active_y and ny > 1) else 0.0
                dz_val = (child_local_pos_in_obj.z * 2.0/ (nz-1)) if (active_z and nz > 1) else 0.0

                default_small_delta = 1.0
                if active_x and nx > 1 and abs(dx_val) < 1e-5: dx_val = default_small_delta * (1 if child_local_pos_in_obj.x >=0 else -1) if abs(child_local_pos_in_obj.x) <1e-5 else dx_val
                if active_y and ny > 1 and abs(dy_val) < 1e-5: dy_val = default_small_delta * (1 if child_local_pos_in_obj.y >=0 else -1) if abs(child_local_pos_in_obj.y) <1e-5 else dy_val
                if active_z and nz > 1 and abs(dz_val) < 1e-5: dz_val = default_small_delta * (1 if child_local_pos_in_obj.z >=0 else -1) if abs(child_local_pos_in_obj.z) <1e-5 else dz_val

                delta_override = (dx_val, dy_val, dz_val)

            final_child_contribution_world = _apply_array_to_shape(
                final_child_contribution_world, current_logical_parent_obj, child_in_list, delta_override)       

            if final_child_contribution_world is None or final_child_contribution_world is lf.emptiness(): continue

        use_morph = utils.get_sdf_param(child_in_list, "sdf_use_morph", False)
        use_clearance = utils.get_sdf_param(child_in_list, "sdf_use_clearance", False) and not use_morph
        child_csg_op_type = utils.get_sdf_param(child_in_list, "sdf_csg_operation", "UNION")
        
        # Each child now provides its own blend factor.
        child_blend_factor = float(utils.get_sdf_param(child_in_list, "sdf_blend_factor", 0.0))
        if utils.is_sdf_group(child_in_list): # Groups use their own blend for csg
            child_csg_op_type = utils.get_sdf_param(child_in_list, "sdf_csg_operation", constants.DEFAULT_GROUP_SETTINGS["sdf_csg_operation"])
        elif utils.is_sdf_canvas(child_in_list): # Canvases use their own blend for csg
            child_csg_op_type = utils.get_sdf_param(child_in_list, "sdf_csg_operation", constants.DEFAULT_CANVAS_SETTINGS["sdf_csg_operation"])
            child_blend_factor = float(utils.get_sdf_param(child_in_list, "sdf_blend_factor", constants.DEFAULT_CANVAS_SETTINGS["sdf_blend_factor"]))
            use_morph = False; use_clearance = False

        if use_morph:
            morph_factor = float(utils.get_sdf_param(child_in_list, "sdf_morph_factor", 0.5))
            try: shape_accumulator = lf.morph(final_child_contribution_world, shape_accumulator, morph_factor)
            except Exception: pass
        elif use_clearance:
            offset_val = float(utils.get_sdf_param(child_in_list, "sdf_clearance_offset", 0.05))
            keep_original = utils.get_sdf_param(child_in_list, "sdf_clearance_keep_original", True)
            try:
                offset_sub = lf.offset(final_child_contribution_world, offset_val)
                shape_accumulator = lf.difference(shape_accumulator, offset_sub)
                if keep_original: shape_accumulator = combine_shapes(shape_accumulator, final_child_contribution_world, child_blend_factor)
            except Exception: pass
        elif child_csg_op_type == "NONE": pass
        elif child_csg_op_type == "UNION":
            shape_accumulator = combine_shapes(shape_accumulator, final_child_contribution_world, child_blend_factor)
        elif child_csg_op_type == "INTERSECT":
            try:
                blend = min(max(0.0, child_blend_factor), 1.0) if child_blend_factor > constants.CACHE_PRECISION else 0.0
                if blend > 0.0 : shape_accumulator = custom_blended_intersection(shape_accumulator, final_child_contribution_world, blend, lf)
                else: shape_accumulator = lf.intersection(shape_accumulator, final_child_contribution_world)
            except Exception: shape_accumulator = lf.emptiness() if _lf_imported_ok else None
        elif child_csg_op_type == "DIFFERENCE":
            try:
                blend = min(max(0.0, child_blend_factor), 1.0) if child_blend_factor > constants.CACHE_PRECISION else 0.0
                if blend > 0.0: shape_accumulator = lf.blend_difference(shape_accumulator, final_child_contribution_world, blend)
                else: shape_accumulator = lf.difference(shape_accumulator, final_child_contribution_world)
            except Exception: pass
        else: shape_accumulator = combine_shapes(shape_accumulator, final_child_contribution_world, child_blend_factor)
            
    return shape_accumulator


def process_sdf_hierarchy(obj: bpy.types.Object, bounds_settings: dict) -> lf.Shape | None:
    context = bpy.context
    if not obj.visible_get(view_layer=context.view_layer):
        return lf.emptiness() if _lf_imported_ok else None

    obj_name = obj.name
    obj_is_sdf_source = utils.is_sdf_source(obj)
    obj_is_group = utils.is_sdf_group(obj)
    obj_is_canvas = utils.is_sdf_canvas(obj)
    
    obj_initial_shape_contribution_world = lf.emptiness() if _lf_imported_ok else None

    if obj_is_sdf_source and not obj_is_canvas:
        unit_shape = reconstruct_shape(obj) 
        if not (unit_shape is None or unit_shape is lf.emptiness()):
            sdf_type_obj = utils.get_sdf_param(obj, "sdf_type", "")
            is_2d_obj = sdf_type_obj in constants._2D_SHAPE_TYPES
            parent_is_canvas_check_obj = obj.parent and utils.is_sdf_canvas(obj.parent)
            if is_2d_obj and not parent_is_canvas_check_obj: 
                depth_obj = utils.get_sdf_param(obj, "sdf_extrusion_depth", constants.DEFAULT_SOURCE_SETTINGS["sdf_extrusion_depth"])
                if float(depth_obj) > 1e-5:
                    try: unit_shape = lf.extrude_z(unit_shape, 0, abs(float(depth_obj)))
                    except Exception: unit_shape = lf.emptiness()
            if utils.get_sdf_param(obj, "sdf_use_shell", False) and not (unit_shape is None or unit_shape is lf.emptiness()):
                offset_obj = float(utils.get_sdf_param(obj, "sdf_shell_offset", constants.DEFAULT_SOURCE_SETTINGS["sdf_shell_offset"]))
                if abs(offset_obj) > 1e-5:
                    try:
                        outer_obj = lf.offset(unit_shape, offset_obj)
                        if offset_obj > 0: unit_shape = lf.difference(outer_obj, unit_shape)
                        else: unit_shape = lf.difference(unit_shape, outer_obj)
                    except Exception: unit_shape = lf.emptiness()
            if not (unit_shape is None or unit_shape is lf.emptiness()):
                obj_initial_shape_contribution_world = apply_blender_transform_to_sdf(unit_shape, obj.matrix_world.inverted())

    elif obj_is_canvas:
        canvas_2d_base_local = lf.emptiness() if _lf_imported_ok else None

        direct_2d_children_list = []
        for c_child_obj in obj.children:
            if c_child_obj and c_child_obj.visible_get(view_layer=context.view_layer) and \
               utils.is_sdf_source(c_child_obj) and utils.get_sdf_param(c_child_obj, "sdf_type", "") in constants._2D_SHAPE_TYPES:
                direct_2d_children_list.append(c_child_obj)
        
        sorted_direct_2d_children = sorted(direct_2d_children_list, key=lambda c: (utils.get_sdf_param(c, "sdf_processing_order", float('inf')), c.name))

        for c2d_item in sorted_direct_2d_children:
            unit_c2d_item_shape = reconstruct_shape(c2d_item)
            if unit_c2d_item_shape is None or unit_c2d_item_shape is lf.emptiness(): continue

            mat_c2d_item_rel_to_canvas = obj.matrix_world.inverted() @ c2d_item.matrix_world
            mat_c2d_item_rel_inv = mat_c2d_item_rel_to_canvas.inverted()
            
            X_cv, Y_cv, Z_cv_dummy = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
            x_remap_cv = mat_c2d_item_rel_inv[0][0]*X_cv + mat_c2d_item_rel_inv[0][1]*Y_cv + mat_c2d_item_rel_inv[0][3]
            y_remap_cv = mat_c2d_item_rel_inv[1][0]*X_cv + mat_c2d_item_rel_inv[1][1]*Y_cv + mat_c2d_item_rel_inv[1][3]
            c2d_item_in_canvas_local_xy = unit_c2d_item_shape.remap(x_remap_cv, y_remap_cv, Z_cv_dummy)

            if c2d_item_in_canvas_local_xy is None or c2d_item_in_canvas_local_xy is lf.emptiness(): continue

            c2d_item_csg_op = utils.get_sdf_param(c2d_item, "sdf_csg_operation", "UNION")
            # Get blend factor from the 2D child itself
            c2d_blend_factor = float(utils.get_sdf_param(c2d_item, "sdf_blend_factor", 0.0))

            if c2d_item_csg_op == "UNION": canvas_2d_base_local = combine_shapes(canvas_2d_base_local, c2d_item_in_canvas_local_xy, c2d_blend_factor)
            elif c2d_item_csg_op == "DIFFERENCE": canvas_2d_base_local = lf.blend_difference(canvas_2d_base_local, c2d_item_in_canvas_local_xy, c2d_blend_factor)
            elif c2d_item_csg_op == "INTERSECT": canvas_2d_base_local = custom_blended_intersection(canvas_2d_base_local, c2d_item_in_canvas_local_xy, c2d_blend_factor, lf)

        obj_processes_linked_children_canvas = obj.get(constants.SDF_PROCESS_LINKED_CHILDREN_PROP, False)
        if utils.is_sdf_linked(obj) and obj_processes_linked_children_canvas:
            linked_target_canvas = utils.get_effective_sdf_object(obj)
            if linked_target_canvas and linked_target_canvas != obj and utils.is_sdf_canvas(linked_target_canvas):
                linked_canvas_2d_children_list = []
                for linked_c_child_obj in linked_target_canvas.children:
                     if linked_c_child_obj and linked_c_child_obj.visible_get(view_layer=context.view_layer) and \
                        utils.is_sdf_source(linked_c_child_obj) and utils.get_sdf_param(linked_c_child_obj, "sdf_type", "") in constants._2D_SHAPE_TYPES:
                         linked_canvas_2d_children_list.append(linked_c_child_obj)
                
                sorted_linked_canvas_2d_children = sorted(linked_canvas_2d_children_list, key=lambda c: (utils.get_sdf_param(c, "sdf_processing_order", float('inf')), c.name))

                for linked_c2d_item in sorted_linked_canvas_2d_children:
                    unit_linked_c2d_item_shape = reconstruct_shape(linked_c2d_item)
                    if unit_linked_c2d_item_shape is None or unit_linked_c2d_item_shape is lf.emptiness(): continue

                    transform_of_linked_c2d_rel_to_its_actual_parent = linked_c2d_item.matrix_local.copy() if linked_c2d_item.parent == linked_target_canvas else (linked_target_canvas.matrix_world.inverted() @ linked_c2d_item.matrix_world)

                    mat_linked_c2d_item_final_local_inv = transform_of_linked_c2d_rel_to_its_actual_parent.inverted()

                    X_lcv, Y_lcv, Z_lcv_dummy = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
                    x_remap_lcv = mat_linked_c2d_item_final_local_inv[0][0]*X_lcv + mat_linked_c2d_item_final_local_inv[0][1]*Y_lcv + mat_linked_c2d_item_final_local_inv[0][3]
                    y_remap_lcv = mat_linked_c2d_item_final_local_inv[1][0]*X_lcv + mat_linked_c2d_item_final_local_inv[1][1]*Y_lcv + mat_linked_c2d_item_final_local_inv[1][3]
                    linked_c2d_item_in_canvas_local_xy = unit_linked_c2d_item_shape.remap(x_remap_lcv, y_remap_lcv, Z_lcv_dummy)
                    
                    if linked_c2d_item_in_canvas_local_xy is None or linked_c2d_item_in_canvas_local_xy is lf.emptiness(): continue

                    linked_c2d_item_csg_op = utils.get_sdf_param(linked_c2d_item, "sdf_csg_operation", "UNION")
                    # Get blend factor from the linked 2D child itself
                    linked_c2d_blend_factor = float(utils.get_sdf_param(linked_c2d_item, "sdf_blend_factor", 0.0))

                    if linked_c2d_item_csg_op == "UNION": canvas_2d_base_local = combine_shapes(canvas_2d_base_local, linked_c2d_item_in_canvas_local_xy, linked_c2d_blend_factor)
                    elif linked_c2d_item_csg_op == "DIFFERENCE": canvas_2d_base_local = lf.blend_difference(canvas_2d_base_local, linked_c2d_item_in_canvas_local_xy, linked_c2d_blend_factor)
                    elif linked_c2d_item_csg_op == "INTERSECT": canvas_2d_base_local = custom_blended_intersection(canvas_2d_base_local, linked_c2d_item_in_canvas_local_xy, linked_c2d_blend_factor, lf)

        if not (canvas_2d_base_local is None or canvas_2d_base_local is lf.emptiness()):
            canvas_3d_final_local = lf.emptiness()
            use_revolve_canvas = utils.get_sdf_param(obj, "sdf_canvas_use_revolve", False)
            if use_revolve_canvas:
                try:
                    profile_for_revolve = lf.intersection(canvas_2d_base_local, libfive_shape_module.Shape.X()) 
                    if not (profile_for_revolve is None or profile_for_revolve is lf.emptiness()):
                        if hasattr(lf, 'revolve_y'): canvas_3d_final_local = lf.revolve_y(profile_for_revolve)
                except Exception: pass
            else:
                canvas_extrusion_depth = float(utils.get_sdf_param(obj, "sdf_extrusion_depth", constants.DEFAULT_CANVAS_SETTINGS["sdf_extrusion_depth"]))
                if canvas_extrusion_depth > 1e-5:
                    try: canvas_3d_final_local = lf.extrude_z(canvas_2d_base_local, 0, canvas_extrusion_depth)
                    except Exception: pass
            
            if not (canvas_3d_final_local is None or canvas_3d_final_local is lf.emptiness()):
                obj_initial_shape_contribution_world = apply_blender_transform_to_sdf(canvas_3d_final_local, obj.matrix_world.inverted())

    current_processing_shape = obj_initial_shape_contribution_world

    current_processing_shape = _process_children_recursive(
        children_owner_obj=obj, current_logical_parent_obj=obj, 
        shape_accumulator=current_processing_shape,
        bounds_settings=bounds_settings, context=context,
        is_processing_as_linked_child_instance=False 
    )

    obj_processes_linked_children = obj.get(constants.SDF_PROCESS_LINKED_CHILDREN_PROP, False) 
    if utils.is_sdf_linked(obj) and obj_processes_linked_children:
        linked_target = utils.get_effective_sdf_object(obj)
        if linked_target and linked_target != obj:

            current_processing_shape = _process_children_recursive(
                children_owner_obj=linked_target, current_logical_parent_obj=obj, 
                shape_accumulator=current_processing_shape,
                bounds_settings=bounds_settings, context=context,
                is_processing_as_linked_child_instance=True
            )

    if obj_is_group:
        if not (current_processing_shape is None or current_processing_shape is lf.emptiness()):
            shape_after_mods = current_processing_shape
            def _apply_local_modifier(current_shape, obj_for_local_space, modifier_func, *args):
                if current_shape is None or current_shape is lf.emptiness(): return current_shape
                shape_in_local = lf.emptiness()
                mat_obj_l2w = obj_for_local_space.matrix_world
                X_loc,Y_loc,Z_loc = libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z()
                try:
                    xp_loc=mat_obj_l2w[0][0]*X_loc+mat_obj_l2w[0][1]*Y_loc+mat_obj_l2w[0][2]*Z_loc+mat_obj_l2w[0][3]
                    yp_loc=mat_obj_l2w[1][0]*X_loc+mat_obj_l2w[1][1]*Y_loc+mat_obj_l2w[1][2]*Z_loc+mat_obj_l2w[1][3]
                    zp_loc=mat_obj_l2w[2][0]*X_loc+mat_obj_l2w[2][1]*Y_loc+mat_obj_l2w[2][2]*Z_loc+mat_obj_l2w[2][3]
                    shape_in_local = current_shape.remap(xp_loc, yp_loc, zp_loc)
                except Exception: return current_shape
                if shape_in_local is None or shape_in_local is lf.emptiness(): return current_shape
                modified_local = modifier_func(shape_in_local, *args)
                if modified_local is None or modified_local is lf.emptiness(): return lf.emptiness()
                return apply_blender_transform_to_sdf(modified_local, obj_for_local_space.matrix_world.inverted())

            group_self_blend_factor = float(utils.get_sdf_param(obj, "sdf_blend_factor", constants.DEFAULT_GROUP_SETTINGS["sdf_blend_factor"]))
            if utils.get_sdf_param(obj, "sdf_group_symmetry_x", False): shape_after_mods = _apply_local_modifier(shape_after_mods, obj, blended_symmetric_x, group_self_blend_factor)
            if utils.get_sdf_param(obj, "sdf_group_symmetry_y", False): shape_after_mods = _apply_local_modifier(shape_after_mods, obj, blended_symmetric_y, group_self_blend_factor)
            if utils.get_sdf_param(obj, "sdf_group_symmetry_z", False): shape_after_mods = _apply_local_modifier(shape_after_mods, obj, blended_symmetric_z, group_self_blend_factor)

            if utils.get_sdf_param(obj, "sdf_group_taper_z_active", False):
                h_tpr=max(1e-5,float(utils.get_sdf_param(obj,"sdf_group_taper_z_height",1.0))); f_tpr=max(0.0,float(utils.get_sdf_param(obj,"sdf_group_taper_z_factor",0.5))); bs_tpr=max(1e-5,float(utils.get_sdf_param(obj,"sdf_group_taper_z_base_scale",1.0)))
                def taper_fn(s_l,h,f,bs): return lf.taper_xy_z(s_l,(0,0,0),h,f,bs)
                shape_after_mods = _apply_local_modifier(shape_after_mods, obj, taper_fn, h_tpr, f_tpr, bs_tpr)

            if utils.get_sdf_param(obj, "sdf_group_shear_x_by_y_active", False):
                h_shr=max(1e-5,float(utils.get_sdf_param(obj,"sdf_group_shear_x_by_y_height",1.0))); o_shr=float(utils.get_sdf_param(obj,"sdf_group_shear_x_by_y_offset",0.5)); bo_shr=float(utils.get_sdf_param(obj,"sdf_group_shear_x_by_y_base_offset",0.0))
                def shear_fn(s_l,h,o,bo):
                    if hasattr(lf,'shear_x_y'): return lf.shear_x_y(s_l,(0,0),h,o,bo)
                    Xshr,Yshr,Zshr=libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z(); ft_shr=Yshr/h; xf_shr=Xshr-(bo*(1.0-ft_shr))-(o*ft_shr)
                    return s_l.remap(xf_shr,Yshr,Zshr)
                shape_after_mods = _apply_local_modifier(shape_after_mods, obj, shear_fn, h_shr, o_shr, bo_shr)

            ar_mode_grp = utils.get_sdf_param(obj, "sdf_group_attract_repel_mode", 'NONE')
            if ar_mode_grp != 'NONE':
                r_ar_grp=max(1e-5,float(utils.get_sdf_param(obj,"sdf_group_attract_repel_radius",0.5))); e_ar_grp=max(0.0,float(utils.get_sdf_param(obj,"sdf_group_attract_repel_exaggerate",1.0)))
                ax_x_grp=utils.get_sdf_param(obj,"sdf_group_attract_repel_axis_x",True); ax_y_grp=utils.get_sdf_param(obj,"sdf_group_attract_repel_axis_y",True); ax_z_grp=utils.get_sdf_param(obj,"sdf_group_attract_repel_axis_z",True)
                prefix_ar = "attract" if ar_mode_grp == 'ATTRACT' else "repel"; sel_ar_fn = None
                if ax_x_grp and ax_y_grp and ax_z_grp: sel_ar_fn = getattr(lf, prefix_ar, None)
                elif ax_x_grp and ax_y_grp: sel_ar_fn = getattr(lf, f"{prefix_ar}_xy", None)
                elif ax_x_grp and ax_z_grp: sel_ar_fn = getattr(lf, f"{prefix_ar}_xz", None)
                elif ax_y_grp and ax_z_grp: sel_ar_fn = getattr(lf, f"{prefix_ar}_yz", None)
                elif ax_x_grp: sel_ar_fn = getattr(lf, f"{prefix_ar}_x", None)
                elif ax_y_grp: sel_ar_fn = getattr(lf, f"{prefix_ar}_y", None)
                elif ax_z_grp: sel_ar_fn = getattr(lf, f"{prefix_ar}_z", None)
                if sel_ar_fn:
                    def ar_fn_wrap(s_l,fn_ar,loc_ar,rad_ar,ex_ar): return fn_ar(s_l,loc_ar,rad_ar,ex_ar)
                    shape_after_mods = _apply_local_modifier(shape_after_mods, obj, ar_fn_wrap, sel_ar_fn, (0,0,0), r_ar_grp, e_ar_grp)
            
            if utils.get_sdf_param(obj, "sdf_group_twirl_active", False):
                tw_ax_grp=utils.get_sdf_param(obj,"sdf_group_twirl_axis",'Z'); tw_am_grp=float(utils.get_sdf_param(obj,"sdf_group_twirl_amount",1.5708)); tw_r_grp=max(1e-5,float(utils.get_sdf_param(obj,"sdf_group_twirl_radius",1.0)))
                tw_fn_name_grp = f"twirl_axis_{tw_ax_grp.lower()}"
                sel_tw_fn = getattr(lf, tw_fn_name_grp, None) if tw_fn_name_grp else None
                if sel_tw_fn:
                    def tw_fn_wrap(s_l,fn_tw,amt_tw,rad_tw,cen_tw): return fn_tw(s_l,amt_tw,rad_tw,cen_tw)
                    shape_after_mods = _apply_local_modifier(shape_after_mods, obj, tw_fn_wrap, sel_tw_fn, tw_am_grp, tw_r_grp, (0,0,0))
            
            current_processing_shape = shape_after_mods

    if current_processing_shape is None and _lf_imported_ok: return lf.emptiness()
    return current_processing_shape