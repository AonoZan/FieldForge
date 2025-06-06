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
    try:
        sym_shape = lf.symmetric_x(shape_in)
        if sym_shape is None or sym_shape is lf.emptiness(): return shape_in
        s_mirrored = lf.reflect_x(shape_in)
        return lf.blend_expt_unit(shape_in, s_mirrored, blend_factor)
    except Exception as e:
        print(f"FieldForge ERROR (blended_symmetric_x): {e}")
        return shape_in

def blended_symmetric_y(shape_in: lf.Shape, blend_factor: float) -> lf.Shape | None:
    if not _lf_imported_ok or shape_in is None or shape_in is lf.emptiness(): return shape_in
    try:
        sym_shape = lf.symmetric_y(shape_in)
        if sym_shape is None or sym_shape is lf.emptiness(): return shape_in
        s_mirrored = lf.reflect_y(shape_in)
        return lf.blend_expt_unit(shape_in, s_mirrored, blend_factor)
    except Exception as e: print(f"FieldForge ERROR (blended_symmetric_y): {e}"); return shape_in

def blended_symmetric_z(shape_in: lf.Shape, blend_factor: float) -> lf.Shape | None:
    if not _lf_imported_ok or shape_in is None or shape_in is lf.emptiness(): return shape_in
    try:
        sym_shape = lf.symmetric_z(shape_in)
        if sym_shape is None or sym_shape is lf.emptiness(): return shape_in
        s_mirrored = lf.reflect_z(shape_in)
        return lf.blend_expt_unit(shape_in, s_mirrored, blend_factor)
    except Exception as e: print(f"FieldForge ERROR (blended_symmetric_z): {e}"); return shape_in

def process_sdf_hierarchy(obj: bpy.types.Object, bounds_settings: dict) -> lf.Shape | None:
    context = bpy.context
    if not obj.visible_get(view_layer=context.view_layer):
        return lf.emptiness()
    def get_sort_key_for_processing(child_obj_param):
        order = child_obj_param.get("sdf_processing_order", float('inf'))
        return (order, child_obj_param.name)

    obj_name = obj.name
    obj_is_sdf_source = utils.is_sdf_source(obj)
    obj_is_group = utils.is_sdf_group(obj)
    obj_is_canvas = utils.get_sdf_param(obj, constants.SDF_CANVAS_MARKER, False)
    current_scene_shape = lf.emptiness() if _lf_imported_ok else None
    sorted_canvas_children = []

    use_revolve = False

    parent_is_canvas = False
    if obj.parent and obj.parent.get(constants.SDF_CANVAS_MARKER, False):
        parent_is_canvas = True
    
    parent_array_mode = 'NONE'
    is_obj_an_arraying_group = False
    if obj_is_group:
        parent_array_mode = utils.get_sdf_param(obj, "sdf_main_array_mode", 'NONE')
        if parent_array_mode != 'NONE':
            is_obj_an_arraying_group = True

    if obj_is_sdf_source and not obj_is_canvas:
        sdf_type = utils.get_sdf_param(obj, "sdf_type", "")
        if sdf_type in constants._2D_SHAPE_TYPES and parent_is_canvas:
            current_scene_shape = lf.emptiness() if _lf_imported_ok else None
        else:
            unit_shape_modified_by_own_ops = reconstruct_shape(obj)
            sdf_type = utils.get_sdf_param(obj, "sdf_type", "")
            is_2d_shape_for_extrude = sdf_type in constants._2D_SHAPE_TYPES
            if is_2d_shape_for_extrude and not (unit_shape_modified_by_own_ops is None or unit_shape_modified_by_own_ops is lf.emptiness()):
                depth = utils.get_sdf_param(obj, "sdf_extrusion_depth", constants.DEFAULT_SOURCE_SETTINGS["sdf_extrusion_depth"])
                if float(depth) > 1e-5:
                    try: unit_shape_modified_by_own_ops = lf.extrude_z(unit_shape_modified_by_own_ops, 0, abs(float(depth)))
                    except Exception as e: print(f"FieldForge ERROR (extrude_z for {obj_name}): {e}"); unit_shape_modified_by_own_ops = lf.emptiness()

            if utils.get_sdf_param(obj, "sdf_use_shell", False) and not (unit_shape_modified_by_own_ops is None or unit_shape_modified_by_own_ops is lf.emptiness()):
                offset = float(utils.get_sdf_param(obj, "sdf_shell_offset", constants.DEFAULT_SOURCE_SETTINGS["sdf_shell_offset"]))
                if abs(offset) > 1e-5:
                    try:
                        outer_surface = lf.offset(unit_shape_modified_by_own_ops, offset)
                        if offset > 0: unit_shape_modified_by_own_ops = lf.difference(outer_surface, unit_shape_modified_by_own_ops)
                        else: unit_shape_modified_by_own_ops = lf.difference(unit_shape_modified_by_own_ops, outer_surface)
                    except Exception as e: print(f"FieldForge ERROR (shell for {obj_name}): {e}"); unit_shape_modified_by_own_ops = lf.emptiness()

            array_mode = utils.get_sdf_param(obj, "sdf_main_array_mode", 'NONE')
            center_on_origin = utils.get_sdf_param(obj, "sdf_array_center_on_origin", True)
            if array_mode != 'NONE' and not (unit_shape_modified_by_own_ops is None or unit_shape_modified_by_own_ops is lf.emptiness()):
                current_shape_before_array = unit_shape_modified_by_own_ops
                if array_mode == 'LINEAR':
                    active_x=utils.get_sdf_param(obj, "sdf_array_active_x",0); active_y=utils.get_sdf_param(obj, "sdf_array_active_y",0) and active_x; active_z=utils.get_sdf_param(obj, "sdf_array_active_z",0) and active_y
                    nx=max(1,int(utils.get_sdf_param(obj, "sdf_array_count_x",2))) if active_x else 1
                    ny=max(1,int(utils.get_sdf_param(obj, "sdf_array_count_y",2))) if active_y else 1
                    nz=max(1,int(utils.get_sdf_param(obj, "sdf_array_count_z",2))) if active_z else 1
                    dx_val=float(utils.get_sdf_param(obj, "sdf_array_delta_x",1)) if active_x else 0; dy_val=float(utils.get_sdf_param(obj, "sdf_array_delta_y",1)) if active_y else 0; dz_val=float(utils.get_sdf_param(obj, "sdf_array_delta_z",1)) if active_z else 0
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
                                if not (unit_shape_modified_by_own_ops is None or unit_shape_modified_by_own_ops is lf.emptiness()):
                                    X,Y,Z=libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z()
                                    unit_shape_modified_by_own_ops=unit_shape_modified_by_own_ops.remap(X-center_shift_x,Y-center_shift_y,Z-center_shift_z)
                    except Exception as e: print(f"FieldForge ERROR (Linear Array for {obj_name}): {e}"); unit_shape_modified_by_own_ops=current_shape_before_array
                elif array_mode == 'RADIAL':
                    count=max(1,int(utils.get_sdf_param(obj, "sdf_radial_count",1))); center_prop=utils.get_sdf_param(obj, "sdf_radial_center",(0.0,0.0))
                    if count > 1:
                        try: center_xy_pivot=(float(center_prop[0]),float(center_prop[1]))
                        except: center_xy_pivot=(0.0,0.0); print(f"FieldForge WARN: Invalid radial center on {obj_name}, using (0,0).")
                        try:
                            unit_shape_modified_by_own_ops=lf.array_polar_z(current_shape_before_array,count,center_xy_pivot)
                            if center_on_origin and (abs(center_xy_pivot[0])>1e-6 or abs(center_xy_pivot[1])>1e-6):
                                if not (unit_shape_modified_by_own_ops is None or unit_shape_modified_by_own_ops is lf.emptiness()):
                                    X,Y,Z=libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z()
                                    unit_shape_modified_by_own_ops=unit_shape_modified_by_own_ops.remap(X+center_xy_pivot[0],Y+center_xy_pivot[1],Z)
                        except Exception as e: print(f"FieldForge ERROR (Radial Array for {obj_name}): {e}"); unit_shape_modified_by_own_ops=current_shape_before_array

            if not (unit_shape_modified_by_own_ops is None or unit_shape_modified_by_own_ops is lf.emptiness()):
                try:
                    current_scene_shape = apply_blender_transform_to_sdf(unit_shape_modified_by_own_ops, obj.matrix_world.inverted())
                except Exception as e:
                    print(f"FieldForge ERROR (transforming self for {obj_name}): {e}")
                    current_scene_shape = lf.emptiness() if _lf_imported_ok else None
    elif obj_is_canvas:
        # --- Process Canvas Object ---
        canvas_2d_shape_local = lf.emptiness() if _lf_imported_ok else None
        canvas_extrusion_depth = float(utils.get_sdf_param(obj, "sdf_extrusion_depth", constants.DEFAULT_CANVAS_SETTINGS["sdf_extrusion_depth"]))
        canvas_child_blend = float(utils.get_sdf_param(obj, "sdf_canvas_child_blend_factor", constants.DEFAULT_CANVAS_SETTINGS["sdf_canvas_child_blend_factor"]))
        use_revolve = utils.get_sdf_param(obj, "sdf_canvas_use_revolve", False)
        canvas_children_2d = []
        for child_candidate in obj.children:
            if child_candidate and child_candidate.visible_get(view_layer=context.view_layer) and \
               utils.is_sdf_source(child_candidate) and \
               child_candidate.get("sdf_type") in constants._2D_SHAPE_TYPES:
                canvas_children_2d.append(child_candidate)

        sorted_canvas_children = sorted(canvas_children_2d, key=get_sort_key_for_processing)

        for canvas_child in sorted_canvas_children:
            unit_2d_child_shape = reconstruct_shape(canvas_child)
            if unit_2d_child_shape is None or unit_2d_child_shape is lf.emptiness():
                continue

            child_shape_in_its_own_local_space = unit_2d_child_shape

            mat_child_relative_to_canvas = obj.matrix_world.inverted() @ canvas_child.matrix_world
            mat_child_relative_to_canvas_inv = mat_child_relative_to_canvas.inverted()

            X, Y, Z_dummy = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()

            x_in_child_local = mat_child_relative_to_canvas_inv[0][0] * X + mat_child_relative_to_canvas_inv[0][1] * Y + mat_child_relative_to_canvas_inv[0][3]
            y_in_child_local = mat_child_relative_to_canvas_inv[1][0] * X + mat_child_relative_to_canvas_inv[1][1] * Y + mat_child_relative_to_canvas_inv[1][3]

            child_2d_shape_in_canvas_local_xy = unit_2d_child_shape.remap(x_in_child_local, y_in_child_local, Z_dummy)

            if child_2d_shape_in_canvas_local_xy is None or child_2d_shape_in_canvas_local_xy is lf.emptiness():
                continue

            child_csg_op = utils.get_sdf_param(canvas_child, "sdf_csg_operation", "UNION")
            canvas_child_blend_default = constants.DEFAULT_CANVAS_SETTINGS.get("sdf_canvas_child_blend_factor", 0.0)
            canvas_child_blend = float(utils.get_sdf_param(canvas_child, "sdf_child_blend_factor", canvas_child_blend_default ))

            if child_csg_op == "UNION":
                canvas_2d_shape_local = combine_shapes(canvas_2d_shape_local, child_2d_shape_in_canvas_local_xy, canvas_child_blend)
            elif child_csg_op == "DIFFERENCE":
                canvas_2d_shape_local = lf.blend_difference(canvas_2d_shape_local, child_2d_shape_in_canvas_local_xy, canvas_child_blend)
            elif child_csg_op == "INTERSECT":
                canvas_2d_shape_local = custom_blended_intersection(canvas_2d_shape_local, child_2d_shape_in_canvas_local_xy, canvas_child_blend, lf)

        if not (canvas_2d_shape_local is None or canvas_2d_shape_local is lf.emptiness()):
            generated_3d_shape_local = lf.emptiness() if _lf_imported_ok else None

            if use_revolve:
                try:
                    X_coord = libfive_shape_module.Shape.X()
                    positive_x_half_space = X_coord

                    profile_to_revolve = lf.intersection(canvas_2d_shape_local, positive_x_half_space)

                    if not (profile_to_revolve is None or profile_to_revolve is lf.emptiness()):
                        if hasattr(lf, 'revolve_y'):
                            generated_3d_shape_local = lf.revolve_y(profile_to_revolve)
                        else:
                            print(f"FieldForge WARN: lf.revolve_y not found for Canvas {obj_name}. Skipping revolve.")
                            generated_3d_shape_local = lf.emptiness()
                    else:
                        generated_3d_shape_local = lf.emptiness()

                except Exception as e:
                    print(f"FieldForge ERROR (Canvas Revolve for {obj_name}): {e}")
                    generated_3d_shape_local = lf.emptiness() if _lf_imported_ok else None
            else:
                if canvas_extrusion_depth > 1e-5:
                    try:
                        generated_3d_shape_local = lf.extrude_z(canvas_2d_shape_local, 0, canvas_extrusion_depth)
                    except Exception as e:
                        generated_3d_shape_local = lf.emptiness() if _lf_imported_ok else None

            if not (generated_3d_shape_local is None or generated_3d_shape_local is lf.emptiness()):
                current_scene_shape = apply_blender_transform_to_sdf(generated_3d_shape_local, obj.matrix_world.inverted())
        else:
            current_scene_shape = lf.emptiness() if _lf_imported_ok else None
    
    parent_provides_blend_factor = 0.0
    if obj_is_sdf_source:
        parent_provides_blend_factor = float(utils.get_sdf_param(obj, "sdf_child_blend_factor", constants.DEFAULT_SOURCE_SETTINGS["sdf_child_blend_factor"]))
    elif obj_is_group:
        parent_provides_blend_factor = float(utils.get_sdf_param(obj, "sdf_child_blend_factor", constants.DEFAULT_GROUP_SETTINGS["sdf_child_blend_factor"]))

    children_to_process = []
    for child_candidate in obj.children:
        if child_candidate and child_candidate.visible_get(view_layer=context.view_layer):
            if utils.is_sdf_source(child_candidate) or \
               utils.is_sdf_group(child_candidate) or \
               (child_candidate.type == 'EMPTY' and not child_candidate.get(constants.SDF_BOUNDS_MARKER)):
                if obj_is_canvas:
                    is_processed_2d_child_of_canvas = False
                    if utils.is_sdf_source(child_candidate) and child_candidate.get("sdf_type") in constants._2D_SHAPE_TYPES:
                        if child_candidate.parent == obj:
                             is_processed_2d_child_of_canvas = True
                    
                    if is_processed_2d_child_of_canvas:
                        continue
                
                children_to_process.append(child_candidate)

    sorted_children = sorted(children_to_process, key=get_sort_key_for_processing)

    for child in sorted_children:

        child_name = child.name
        is_current_obj_valid_loft_base = obj_is_sdf_source and \
                                         utils.get_sdf_param(obj, "sdf_use_loft", False) and \
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
                    if not (lofted_shape_local_to_obj is None or lofted_shape_local_to_obj is lf.emptiness()):
                        lofted_contribution_world = apply_blender_transform_to_sdf(lofted_shape_local_to_obj, obj.matrix_world.inverted())
                except Exception as e:
                    print(f"FieldForge ERROR (lofting {obj_name} to {child_name}): {e}")
            current_scene_shape = combine_shapes(current_scene_shape, lofted_contribution_world, parent_provides_blend_factor)
            continue

        child_world_shape = process_sdf_hierarchy(child, bounds_settings)
        if child_world_shape is None or child_world_shape is lf.emptiness():
            continue
        final_child_contribution_world = child_world_shape

        # --- Apply Group-Level Array TO THIS CHILD if 'obj' is an arraying Group ---
        if is_obj_an_arraying_group:
            child_shape_in_obj_local_space = lf.emptiness()
            try:
                mat_l2w_obj = obj.matrix_world
                X_cl, Y_cl, Z_cl = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
                x_expr_cl = mat_l2w_obj[0][0]*X_cl + mat_l2w_obj[0][1]*Y_cl + mat_l2w_obj[0][2]*Z_cl + mat_l2w_obj[0][3]
                y_expr_cl = mat_l2w_obj[1][0]*X_cl + mat_l2w_obj[1][1]*Y_cl + mat_l2w_obj[1][2]*Z_cl + mat_l2w_obj[1][3]
                z_expr_cl = mat_l2w_obj[2][0]*X_cl + mat_l2w_obj[2][1]*Y_cl + mat_l2w_obj[2][2]*Z_cl + mat_l2w_obj[2][3]
                child_shape_in_obj_local_space = child_world_shape.remap(x_expr_cl, y_expr_cl, z_expr_cl)
            except Exception as e:
                print(f"FF ERROR (Group Array Child: World to Local for {child.name} under {obj_name}): {e}")
                child_shape_in_obj_local_space = lf.emptiness()

            if not (child_shape_in_obj_local_space is None or child_shape_in_obj_local_space is lf.emptiness()):
                arrayed_child_local = child_shape_in_obj_local_space # Base for arraying this child

                if parent_array_mode == 'LINEAR':
                    active_x = utils.get_sdf_param(obj, "sdf_array_active_x", False)
                    active_y = utils.get_sdf_param(obj, "sdf_array_active_y", False) and active_x
                    active_z = utils.get_sdf_param(obj, "sdf_array_active_z", False) and active_y

                    active_xs, active_ys, active_zs = obj.scale

                    child_local_pos_in_obj = (obj.matrix_world.inverted() @ child.matrix_world).translation

                    nx = max(1, int(utils.get_sdf_param(obj, "sdf_array_count_x", 2))) if active_x else 1
                    ny = max(1, int(utils.get_sdf_param(obj, "sdf_array_count_y", 2))) if active_y else 1
                    nz = max(1, int(utils.get_sdf_param(obj, "sdf_array_count_z", 2))) if active_z else 1

                    dx_val = (child_local_pos_in_obj.x * 2.0 / (nx-1)) if (active_x and nx > 1) else 0
                    dy_val = (child_local_pos_in_obj.y * 2.0 / (ny-1)) if (active_y and ny > 1) else 0
                    dz_val = (child_local_pos_in_obj.z * 2.0 / (nz-1)) if (active_z and nz > 1) else 0

                    default_small_delta = 1.0
                    if active_x and nx > 1 and abs(dx_val) < 1e-5: dx_val = default_small_delta * (1 if child_local_pos_in_obj.x >=0 else -1) if abs(child_local_pos_in_obj.x) <1e-5 else dx_val
                    if active_y and ny > 1 and abs(dy_val) < 1e-5: dy_val = default_small_delta * (1 if child_local_pos_in_obj.y >=0 else -1) if abs(child_local_pos_in_obj.y) <1e-5 else dy_val
                    if active_z and nz > 1 and abs(dz_val) < 1e-5: dz_val = default_small_delta * (1 if child_local_pos_in_obj.z >=0 else -1) if abs(child_local_pos_in_obj.z) <1e-5 else dz_val

                    array_applied_to_child = False
                    try:
                        if active_z:
                            if nx > 1 or ny > 1 or nz > 1: arrayed_child_local = lf.array_xyz(child_shape_in_obj_local_space, nx, ny, nz, (dx_val, dy_val, dz_val)); array_applied_to_child=True
                        elif active_y:
                            if nx > 1 or ny > 1: arrayed_child_local = lf.array_xy(child_shape_in_obj_local_space, nx, ny, (dx_val, dy_val)); array_applied_to_child=True
                        elif active_x:
                            if nx > 1: arrayed_child_local = lf.array_x(child_shape_in_obj_local_space, nx, dx_val); array_applied_to_child=True
                        
                        if array_applied_to_child:
                                center_shift_x = -(dx_val*(nx-1))
                                center_shift_y = -(dy_val*(ny-1))
                                center_shift_z = -(dz_val*(nz-1))
                                if abs(center_shift_x)>1e-6 or abs(center_shift_y)>1e-6 or abs(center_shift_z)>1e-6:
                                    X_arr_c,Y_arr_c,Z_arr_c=libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z()
                                    arrayed_child_local=arrayed_child_local.remap(X_arr_c-center_shift_x,Y_arr_c-center_shift_y,Z_arr_c-center_shift_z)

                    except Exception as e_arr_child_lin: print(f"FF ERROR (Group Child Linear Array for {child.name}): {e_arr_child_lin}")
                
                elif parent_array_mode == 'RADIAL':
                    count_rad = max(1, int(utils.get_sdf_param(obj, "sdf_radial_count", 1)))
                    center_prop_rad = utils.get_sdf_param(obj, "sdf_radial_center", (0.0,0.0))
                    if count_rad > 1:
                        try: center_xy_pivot_rad = (float(center_prop_rad[0]), float(center_prop_rad[1]))
                        except: center_xy_pivot_rad = (0.0,0.0)
                        try:
                            arrayed_child_local = lf.array_polar_z(child_shape_in_obj_local_space, count_rad, center_xy_pivot_rad)
                            if (abs(center_xy_pivot_rad[0])>1e-6 or abs(center_xy_pivot_rad[1])>1e-6):
                                X_rad_c,Y_rad_c,Z_rad_c=libfive_shape_module.Shape.X(),libfive_shape_module.Shape.Y(),libfive_shape_module.Shape.Z()
                                arrayed_child_local=arrayed_child_local.remap(X_rad_c+center_xy_pivot_rad[0],Y_rad_c+center_xy_pivot_rad[1],Z_rad_c)
                        except Exception as e_rad_arr_child: print(f"FF ERROR (Group Child Radial Array for {child.name}): {e_rad_arr_child}")
                if not (arrayed_child_local is None or arrayed_child_local is lf.emptiness()):
                    final_child_contribution_world = apply_blender_transform_to_sdf(arrayed_child_local, obj.matrix_world.inverted())
                else:
                    final_child_contribution_world = lf.emptiness() if _lf_imported_ok else None

        processed_child_subtree_world = final_child_contribution_world

        if utils.is_sdf_source(child):
            use_morph = utils.get_sdf_param(child, "sdf_use_morph", constants.DEFAULT_SOURCE_SETTINGS["sdf_use_morph"])
            use_clearance = utils.get_sdf_param(child, "sdf_use_clearance", constants.DEFAULT_SOURCE_SETTINGS["sdf_use_clearance"]) and not use_morph
            child_csg_op_type = utils.get_sdf_param(child, "sdf_csg_operation", constants.DEFAULT_SOURCE_SETTINGS["sdf_csg_operation"])

            if use_morph:
                morph_factor = float(utils.get_sdf_param(child, "sdf_morph_factor", constants.DEFAULT_SOURCE_SETTINGS["sdf_morph_factor"]))
                try: current_scene_shape = lf.morph(processed_child_subtree_world, current_scene_shape, morph_factor)
                except Exception as e: print(f"FieldForge ERROR (morphing {obj_name} with {child_name}): {e}")
            elif use_clearance:
                offset_val = float(utils.get_sdf_param(child, "sdf_clearance_offset", constants.DEFAULT_SOURCE_SETTINGS["sdf_clearance_offset"]))
                keep_original = utils.get_sdf_param(child, "sdf_clearance_keep_original", constants.DEFAULT_SOURCE_SETTINGS["sdf_clearance_keep_original"])
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
                if not ((current_scene_shape is None or current_scene_shape is lf.emptiness()) or \
                   (processed_child_subtree_world is None or processed_child_subtree_world is lf.emptiness())):
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
    if obj_is_group:
        if not (current_scene_shape is None or current_scene_shape is lf.emptiness()):
            if not (current_scene_shape is None or current_scene_shape is lf.emptiness()):
                # --- Apply Group Symmetry ---
                symmetry_x = utils.get_sdf_param(obj, "sdf_group_symmetry_x", False)
                symmetry_y = utils.get_sdf_param(obj, "sdf_group_symmetry_y", False)
                symmetry_z = utils.get_sdf_param(obj, "sdf_group_symmetry_z", False)

                clamped_blend_symmetry = min(max(0.001, parent_provides_blend_factor), 1.0)

                shape_after_symmetry = current_scene_shape
                if symmetry_x or symmetry_y or symmetry_z:
                    group_local_coords_shape_for_sym = lf.emptiness() if _lf_imported_ok else None
                    try:
                        mat_l2w_sym = obj.matrix_world
                        X_s, Y_s, Z_s = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
                        x_expr_s = mat_l2w_sym[0][0] * X_s + mat_l2w_sym[0][1] * Y_s + mat_l2w_sym[0][2] * Z_s + mat_l2w_sym[0][3]
                        y_expr_s = mat_l2w_sym[1][0] * X_s + mat_l2w_sym[1][1] * Y_s + mat_l2w_sym[1][2] * Z_s + mat_l2w_sym[1][3]
                        z_expr_s = mat_l2w_sym[2][0] * X_s + mat_l2w_sym[2][1] * Y_s + mat_l2w_sym[2][2] * Z_s + mat_l2w_sym[2][3]
                        group_local_coords_shape_for_sym = current_scene_shape.remap(x_expr_s, y_expr_s, z_expr_s)
                    except Exception as e:
                        print(f"FieldForge ERROR (Group Symmetry: World to Local for {obj_name}): {e}")

                    if not (group_local_coords_shape_for_sym is None or group_local_coords_shape_for_sym is lf.emptiness()):
                        symmetrized_in_local = group_local_coords_shape_for_sym
                        
                        if symmetry_x:
                            symmetrized_in_local = blended_symmetric_x(symmetrized_in_local, clamped_blend_symmetry)
                        if symmetry_y:
                            if symmetrized_in_local is not None and symmetrized_in_local is not lf.emptiness():
                                symmetrized_in_local = blended_symmetric_y(symmetrized_in_local, clamped_blend_symmetry)
                            else:
                                symmetrized_in_local = group_local_coords_shape_for_sym
                        if symmetry_z:
                            if symmetrized_in_local is not None and symmetrized_in_local is not lf.emptiness():
                                symmetrized_in_local = blended_symmetric_z(symmetrized_in_local, clamped_blend_symmetry)
                            else:
                                symmetrized_in_local = group_local_coords_shape_for_sym
                        
                        shape_after_symmetry = apply_blender_transform_to_sdf(symmetrized_in_local, obj.matrix_world.inverted())
                        if shape_after_symmetry is None: shape_after_symmetry = lf.emptiness() if _lf_imported_ok else None

                current_scene_shape = shape_after_symmetry

            # --- TAPER (applied to the result of symmetry) ---
            if not (current_scene_shape is None or current_scene_shape is lf.emptiness()):
                taper_z_active = utils.get_sdf_param(obj, "sdf_group_taper_z_active", False)
                shape_after_taper = current_scene_shape

                if taper_z_active:
                    group_local_coords_shape_for_taper = lf.emptiness() if _lf_imported_ok else None
                    try:
                        mat_l2w_taper = obj.matrix_world
                        X_t, Y_t, Z_t = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
                        x_expr_t = mat_l2w_taper[0][0] * X_t + mat_l2w_taper[0][1] * Y_t + mat_l2w_taper[0][2] * Z_t + mat_l2w_taper[0][3]
                        y_expr_t = mat_l2w_taper[1][0] * X_t + mat_l2w_taper[1][1] * Y_t + mat_l2w_taper[1][2] * Z_t + mat_l2w_taper[1][3]
                        z_expr_t = mat_l2w_taper[2][0] * X_t + mat_l2w_taper[2][1] * Y_t + mat_l2w_taper[2][2] * Z_t + mat_l2w_taper[2][3]
                        group_local_coords_shape_for_taper = current_scene_shape.remap(x_expr_t, y_expr_t, z_expr_t)
                    except Exception as e:
                        print(f"FieldForge ERROR (Group Taper: World to Local for {obj_name}): {e}")

                    if not (group_local_coords_shape_for_taper is None or group_local_coords_shape_for_taper is lf.emptiness()):
                        taper_height = float(utils.get_sdf_param(obj, "sdf_group_taper_z_height", 1.0))
                        taper_scale_at_top = float(utils.get_sdf_param(obj, "sdf_group_taper_z_factor", 0.5))
                        taper_base_scale = float(utils.get_sdf_param(obj, "sdf_group_taper_z_base_scale", 1.0))
                        
                        taper_height = max(1e-5, taper_height)
                        taper_scale_at_top = max(0.0, taper_scale_at_top)
                        taper_base_scale = max(1e-5, taper_base_scale)

                        try:
                            tapered_in_local = lf.taper_xy_z(
                                group_local_coords_shape_for_taper,
                                (0.0, 0.0, 0.0),
                                taper_height,
                                taper_scale_at_top,
                                taper_base_scale
                            )
                            
                            shape_after_taper = apply_blender_transform_to_sdf(tapered_in_local, obj.matrix_world.inverted())
                            if shape_after_taper is None:
                                shape_after_taper = lf.emptiness() if _lf_imported_ok else None
                        except AttributeError:
                            print(f"FieldForge WARN: lf.taper_xy_z not found. Is libfive stdlib up to date or correctly wrapped?")
                        except Exception as e_taper:
                            print(f"FieldForge ERROR (Group Tapering {obj_name}): {e_taper}")
                
                current_scene_shape = shape_after_taper

            # --- SHEAR X by Y (applied to the result of taper) ---
            if not (current_scene_shape is None or current_scene_shape is lf.emptiness()):
                shear_x_by_y_active = utils.get_sdf_param(obj, "sdf_group_shear_x_by_y_active", False)
                shape_after_shear = current_scene_shape

                if shear_x_by_y_active:
                    group_local_coords_shape_for_shear = lf.emptiness() if _lf_imported_ok else None
                    try:
                        mat_l2w_shear = obj.matrix_world
                        X_sh, Y_sh, Z_sh = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
                        x_expr_sh = mat_l2w_shear[0][0]*X_sh + mat_l2w_shear[0][1]*Y_sh + mat_l2w_shear[0][2]*Z_sh + mat_l2w_shear[0][3]
                        y_expr_sh = mat_l2w_shear[1][0]*X_sh + mat_l2w_shear[1][1]*Y_sh + mat_l2w_shear[1][2]*Z_sh + mat_l2w_shear[1][3]
                        z_expr_sh = mat_l2w_shear[2][0]*X_sh + mat_l2w_shear[2][1]*Y_sh + mat_l2w_shear[2][2]*Z_sh + mat_l2w_shear[2][3]
                        group_local_coords_shape_for_shear = current_scene_shape.remap(x_expr_sh, y_expr_sh, z_expr_sh)
                    except Exception as e:
                        print(f"FieldForge ERROR (Group Shear XbyY: World to Local for {obj_name}): {e}")

                    if not (group_local_coords_shape_for_shear is None or group_local_coords_shape_for_shear is lf.emptiness()):
                        shear_height = float(utils.get_sdf_param(obj, "sdf_group_shear_x_by_y_height", 1.0))
                        shear_offset = float(utils.get_sdf_param(obj, "sdf_group_shear_x_by_y_offset", 0.5))
                        shear_base_offset = float(utils.get_sdf_param(obj, "sdf_group_shear_x_by_y_base_offset", 0.0))

                        shear_height = max(1e-5, shear_height)

                        try:
                            if hasattr(lf, 'shear_x_y'):
                                sheared_in_local = lf.shear_x_y(
                                    group_local_coords_shape_for_shear,
                                    (0.0, 0.0),
                                    shear_height,
                                    shear_offset,
                                    shear_base_offset
                                )
                            else:
                                print(f"FieldForge INFO: lf.shear_x_y not found, using direct remap for group {obj_name}.")
                                X_remap, Y_remap, Z_remap = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
                                f_tree = Y_remap / shear_height
                                x_final_remap = X_remap - (shear_base_offset * (1.0 - f_tree)) - (shear_offset * f_tree)
                                sheared_in_local = group_local_coords_shape_for_shear.remap(x_final_remap, Y_remap, Z_remap)

                            shape_after_shear = apply_blender_transform_to_sdf(sheared_in_local, obj.matrix_world.inverted())
                            if shape_after_shear is None:
                                shape_after_shear = lf.emptiness() if _lf_imported_ok else None
                        except Exception as e_shear:
                            print(f"FieldForge ERROR (Group Shearing XbyY for {obj_name}): {e_shear}")

                current_scene_shape = shape_after_shear

            # --- ATTRACT/REPEL ---
            if not (current_scene_shape is None or current_scene_shape is lf.emptiness()):
                ar_mode = utils.get_sdf_param(obj, "sdf_group_attract_repel_mode", 'NONE')
                shape_after_attract_repel = current_scene_shape

                if ar_mode != 'NONE':
                    group_local_coords_shape_for_ar = lf.emptiness() if _lf_imported_ok else None
                    try:
                        mat_l2w_ar = obj.matrix_world
                        X_ar, Y_ar, Z_ar = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
                        x_expr_ar = mat_l2w_ar[0][0]*X_ar + mat_l2w_ar[0][1]*Y_ar + mat_l2w_ar[0][2]*Z_ar + mat_l2w_ar[0][3]
                        y_expr_ar = mat_l2w_ar[1][0]*X_ar + mat_l2w_ar[1][1]*Y_ar + mat_l2w_ar[1][2]*Z_ar + mat_l2w_ar[1][3]
                        z_expr_ar = mat_l2w_ar[2][0]*X_ar + mat_l2w_ar[2][1]*Y_ar + mat_l2w_ar[2][2]*Z_ar + mat_l2w_ar[2][3]
                        group_local_coords_shape_for_ar = current_scene_shape.remap(x_expr_ar, y_expr_ar, z_expr_ar)
                    except Exception as e:
                        print(f"FieldForge ERROR (Group Attract/Repel: World to Local for {obj_name}): {e}")

                    if not (group_local_coords_shape_for_ar is None or group_local_coords_shape_for_ar is lf.emptiness()):
                        ar_radius_val = float(utils.get_sdf_param(obj, "sdf_group_attract_repel_radius", 0.5))
                        ar_exaggerate_val = float(utils.get_sdf_param(obj, "sdf_group_attract_repel_exaggerate", 1.0))

                        ar_radius_val = max(1e-5, ar_radius_val)
                        ar_exaggerate_val = max(0.0, ar_exaggerate_val)

                        locus_local = (0.0, 0.0, 0.0)

                        use_x = utils.get_sdf_param(obj, "sdf_group_attract_repel_axis_x", True)
                        use_y = utils.get_sdf_param(obj, "sdf_group_attract_repel_axis_y", True)
                        use_z = utils.get_sdf_param(obj, "sdf_group_attract_repel_axis_z", True)

                        selected_func = None

                        if ar_mode == 'ATTRACT':
                            if use_x and use_y and use_z: selected_func = getattr(lf, 'attract', None)
                            elif use_x and use_y:         selected_func = getattr(lf, 'attract_xy', None)
                            elif use_x and use_z:         selected_func = getattr(lf, 'attract_xz', None)
                            elif use_y and use_z:         selected_func = getattr(lf, 'attract_yz', None)
                            elif use_x:                   selected_func = getattr(lf, 'attract_x', None)
                            elif use_y:                   selected_func = getattr(lf, 'attract_y', None)
                            elif use_z:                   selected_func = getattr(lf, 'attract_z', None)
                        elif ar_mode == 'REPEL':
                            if use_x and use_y and use_z: selected_func = getattr(lf, 'repel', None)
                            elif use_x and use_y:         selected_func = getattr(lf, 'repel_xy', None)
                            elif use_x and use_z:         selected_func = getattr(lf, 'repel_xz', None)
                            elif use_y and use_z:         selected_func = getattr(lf, 'repel_yz', None)
                            elif use_x:                   selected_func = getattr(lf, 'repel_x', None)
                            elif use_y:                   selected_func = getattr(lf, 'repel_y', None)
                            elif use_z:                   selected_func = getattr(lf, 'repel_z', None)

                        ar_in_local = group_local_coords_shape_for_ar
                        if selected_func:
                            try:
                                ar_in_local = selected_func(
                                    group_local_coords_shape_for_ar,
                                    locus_local,
                                    ar_radius_val,
                                    ar_exaggerate_val
                                )
                            except Exception as e_ar_call:
                                print(f"FieldForge ERROR calling {selected_func.__name__} for {obj_name}: {e_ar_call}")
                        elif use_x or use_y or use_z:
                             print(f"FieldForge WARN: No specific attract/repel function found for the combination of active axes on {obj_name}. Effect might be incorrect or skipped.")

                        shape_after_attract_repel = apply_blender_transform_to_sdf(ar_in_local, obj.matrix_world.inverted())
                        if shape_after_attract_repel is None:
                            shape_after_attract_repel = lf.emptiness() if _lf_imported_ok else None

                current_scene_shape = shape_after_attract_repel

            # --- TWIRL (applied last) ---
            if not (current_scene_shape is None or current_scene_shape is lf.emptiness()):
                twirl_active = utils.get_sdf_param(obj, "sdf_group_twirl_active", False)
                shape_after_twirl = current_scene_shape

                if twirl_active:
                    group_local_coords_shape_for_twirl = lf.emptiness() if _lf_imported_ok else None
                    try: # World to Local
                        mat_l2w_tw = obj.matrix_world
                        X_tw, Y_tw, Z_tw = libfive_shape_module.Shape.X(), libfive_shape_module.Shape.Y(), libfive_shape_module.Shape.Z()
                        x_expr_tw = mat_l2w_tw[0][0]*X_tw + mat_l2w_tw[0][1]*Y_tw + mat_l2w_tw[0][2]*Z_tw + mat_l2w_tw[0][3]
                        y_expr_tw = mat_l2w_tw[1][0]*X_tw + mat_l2w_tw[1][1]*Y_tw + mat_l2w_tw[1][2]*Z_tw + mat_l2w_tw[1][3]
                        z_expr_tw = mat_l2w_tw[2][0]*X_tw + mat_l2w_tw[2][1]*Y_tw + mat_l2w_tw[2][2]*Z_tw + mat_l2w_tw[2][3]
                        group_local_coords_shape_for_twirl = current_scene_shape.remap(x_expr_tw, y_expr_tw, z_expr_tw)
                    except Exception as e:
                        print(f"FieldForge ERROR (Group Twirl: World to Local for {obj_name}): {e}")

                    if not (group_local_coords_shape_for_twirl is None or group_local_coords_shape_for_twirl is lf.emptiness()):
                        tw_axis = utils.get_sdf_param(obj, "sdf_group_twirl_axis", 'Z')
                        tw_amount = float(utils.get_sdf_param(obj, "sdf_group_twirl_amount", 1.5708))
                        tw_radius = float(utils.get_sdf_param(obj, "sdf_group_twirl_radius", 1.0))
                        tw_radius = max(1e-5, tw_radius) # Ensure positive radius

                        twirled_in_local = None
                        try:
                            center_of_twirl = (0.0, 0.0, 0.0) # Group's local origin
                            if tw_axis == 'X':
                                if hasattr(lf, 'twirl_axis_x'):
                                    twirled_in_local = lf.twirl_axis_x(group_local_coords_shape_for_twirl, tw_amount, tw_radius, center_of_twirl)
                                else: print(f"FF WARN: lf.twirl_axis_x not found for {obj_name}.")
                            elif tw_axis == 'Y':
                                if hasattr(lf, 'twirl_axis_y'):
                                    twirled_in_local = lf.twirl_axis_y(group_local_coords_shape_for_twirl, tw_amount, tw_radius, center_of_twirl)
                                else: print(f"FF WARN: lf.twirl_axis_y not found for {obj_name}.")
                            elif tw_axis == 'Z':
                                if hasattr(lf, 'twirl_axis_z'):
                                    twirled_in_local = lf.twirl_axis_z(group_local_coords_shape_for_twirl, tw_amount, tw_radius, center_of_twirl)
                                else: print(f"FF WARN: lf.twirl_axis_z not found for {obj_name}.")
                            
                            if not (twirled_in_local is None or twirled_in_local is lf.emptiness()): # If any twirl function was called and returned a shape
                                shape_after_twirl = apply_blender_transform_to_sdf(twirled_in_local, obj.matrix_world.inverted())
                                if shape_after_twirl is None: shape_after_twirl = lf.emptiness() if _lf_imported_ok else None
                            # If twirl function was not found or failed, shape_after_twirl remains the input to this stage
                        except AttributeError as ae:
                            print(f"FieldForge WARN: Twirl function for axis {tw_axis} not found or error during call: {ae}")
                        except Exception as e_tw:
                            print(f"FieldForge ERROR (Group Twirling {obj_name}): {e_tw}")
                
                current_scene_shape = shape_after_twirl

    if current_scene_shape is None and _lf_imported_ok:
        return lf.emptiness()
    return current_scene_shape