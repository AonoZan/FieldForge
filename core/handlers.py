import bpy
import bmesh
from bpy.app.handlers import persistent
from .. import utils
from .. import constants
from ..ui import operators

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

    for obj, original_mesh in _mesh_data_backup.items():
        if obj and obj.type == 'MESH':
            temp_mesh = obj.data
            obj.data = original_mesh
            if temp_mesh and temp_mesh.users == 0:
                bpy.data.meshes.remove(temp_mesh)

@persistent
def ff_load_post_handler(dummy):
    """
    Ensures the FieldForge selection handler is running after a file loads.
    Forces reset of the running flag and attempts to start the handler.
    """

    operators._selection_handler_running = False
    try:
        operators.start_select_handler_via_timer()
    except AttributeError:
         print("FieldForge ERROR (load_post): Could not find function to start select handler in operators.py")
    except Exception as e:
        print(f"FieldForge ERROR (load_post): Failed to start modal select handler via timer: {e}")

@persistent
def ff_undo_pre_handler(dummy):
    from ..core import update_manager
    update_manager.clear_timers_and_state()

@persistent
def ff_undo_post_handler(dummy):
    from ..core import update_manager
    bpy.app.timers.register(update_manager.initial_update_check_all, first_interval=0.05)