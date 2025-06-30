import bpy
import FieldForge.constants as constants

def create_primitive():
    """
    Creates an SDF Cylinder source object using the FieldForge operator.
    Assumes the bounds_obj is already the active object.
    """
    # Use the operator to add an SDF Cylinder source
    # The operator will automatically parent to the active object (bounds_obj)
    bpy.ops.object.add_sdf_cylinder_source(initial_csg_operation=constants.DEFAULT_SOURCE_SETTINGS["sdf_csg_operation"],
                                           initial_blend_factor=constants.DEFAULT_SOURCE_SETTINGS["sdf_blend_factor"])
    cylinder_obj = bpy.context.active_object

    # Set empty_display_size for visual representation in Blender GUI
    # This is not part of the SDF logic but helps with visual debugging
    cylinder_obj.empty_display_size = 1.0
    cylinder_obj.scale = (0.5, 0.5, 0.5)

    print(f"Created SDF Cylinder: {cylinder_obj.name}")
