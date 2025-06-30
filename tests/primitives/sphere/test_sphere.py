import bpy
import FieldForge.constants as constants

def create_primitive():
    # Get the current active object (which should be the bounds_obj)
    parent_obj = bpy.context.active_object

    # Add an empty object to represent the SDF source
    bpy.ops.object.empty_add(type='SPHERE', location=(0, 0, 0))
    sphere_obj = bpy.context.active_object
    sphere_obj.name = "000_Sphere"

    # Set custom properties for SDF source
    sphere_obj[constants.SDF_PROPERTY_MARKER] = True
    sphere_obj["sdf_type"] = 'sphere'
    
    

    # Set local scale to 0.5, 0.5, 0.5 to make world scale 1.0, 1.0, 1.0 when parented to 2.0 scaled bounds
    sphere_obj.scale = (0.5, 0.5, 0.5)

    # Set empty display size for visual consistency
    sphere_obj.empty_display_size = 1.0

    # Parent the sphere to the active object (which should be the bounds_obj)
    sphere_obj.parent = parent_obj

    print("Created SDF Sphere: " + sphere_obj.name)
