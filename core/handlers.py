import bpy
import bmesh
from bpy.app.handlers import persistent
from .. import utils
from .. import constants

# Global dictionary to store mesh data before saving
_mesh_data_backup = {}

@persistent
def ff_save_pre_handler(dummy):
    """Handler to remove mesh data before saving, storing it for restoration."""
    global _mesh_data_backup
    _mesh_data_backup.clear()  # Clear previous backup

    context = bpy.context
    if not context or not context.scene:
        return

    # Iterate through all bounds in the scene
    for bounds_obj in utils.get_all_bounds_objects(context):
        discard_mesh = utils.get_bounds_setting(bounds_obj, "sdf_discard_mesh_on_save")
        if discard_mesh:
            result_name = bounds_obj.get(constants.SDF_RESULT_OBJ_NAME_PROP)
            result_obj = utils.find_result_object(context, result_name)
            if result_obj.type == 'MESH' and result_obj.data:
                # Store the mesh data
                mesh = result_obj.data
                _mesh_data_backup[result_obj] = mesh
                # Create an empty mesh to replace the current one
                empty_mesh = bpy.data.meshes.new(name=f"{result_obj.name}_temp")
                result_obj.data = empty_mesh

@persistent
def ff_save_post_handler(dummy):
    """Handler to restore mesh data after saving."""
    global _mesh_data_backup

    # Restore mesh data for each object
    for obj, original_mesh in _mesh_data_backup.items():
        if obj and obj.type == 'MESH':
            # Get the temporary mesh
            temp_mesh = obj.data
            # Restore the original mesh
            obj.data = original_mesh
            # Remove the temporary mesh if it's no longer used
            if temp_mesh and temp_mesh.users == 0:
                bpy.data.meshes.remove(temp_mesh)