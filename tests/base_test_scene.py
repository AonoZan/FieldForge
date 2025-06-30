import bpy
import sys
import os
import math
from mathutils import Vector, Matrix
import bmesh

# Get arguments passed after '--'
argv = sys.argv
argv = argv[argv.index("--") + 1:]

# The first argument will be the path to the primitive-specific script
primitive_script_path = argv[0]
output_blend_file = argv[1]
output_obj_file = argv[2]
ground_truth_obj_file = argv[3]

# Clear existing objects
bpy.ops.wm.read_factory_settings(use_empty=True)

# Add the FieldForge addon directory to sys.path
addon_root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if addon_root_path not in sys.path:
    sys.path.append(addon_root_path)

# Enable the FieldForge addon
try:
    bpy.ops.preferences.addon_enable(module='FieldForge')
    print("FieldForge addon enabled.")
except Exception as e:
    print(f"Error enabling FieldForge addon: {e}")
    sys.exit(1)

_libfive_imported = False
try:
    import FieldForge.libfive.stdlib as lf
    _libfive_imported = True
    print("libfive imported successfully.")
except ImportError as e:
    print(f"Error importing libfive: {e}")

if not _libfive_imported:
    print("libfive is not available. Exiting.")
    sys.exit(1)

try:
    import FieldForge.constants as constants
    import FieldForge.utils as utils
    import FieldForge.core.sdf_logic as sdf_logic
    import FieldForge.ui.operators as ff_operators # Import operators
except ImportError as e:
    print(f"Error importing FieldForge modules: {e}")
    sys.exit(1)

# --- Create SDF Bounds object using operator ---
bpy.ops.object.add_sdf_bounds(location=(0, 0, 0), bounds_name_prefix="TestScene")
bounds_obj = bpy.context.active_object

if not bounds_obj or not bounds_obj.get(constants.SDF_BOUNDS_MARKER):
    print("Failed to create SDF Bounds object using operator.")
    sys.exit(1)

# Set bounds object scale to 2.0, 2.0, 2.0
bounds_obj.scale = (2.0, 2.0, 2.0)

# Ensure the bounds object is active for context for primitive creation
bpy.context.view_layer.objects.active = bounds_obj

# --- Execute the primitive-specific script ---
# This script is expected to define a function like 'create_primitive()'
# that adds the primitive to the scene and parents it to the active object (bounds_obj).

# Add the directory of the primitive script to sys.path temporarily
primitive_script_dir = os.path.dirname(primitive_script_path)
if primitive_script_dir not in sys.path:
    sys.path.append(primitive_script_dir)

primitive_module_name = os.path.basename(primitive_script_path).replace('.py', '')

try:
    primitive_module = __import__(primitive_module_name)
    if hasattr(primitive_module, 'create_primitive'):
        primitive_module.create_primitive() # Call without bounds_obj argument
    else:
        print(f"Error: Primitive script {primitive_script_path} does not have a 'create_primitive' function.")
        sys.exit(1)
except ImportError as e:
    print(f"Error importing primitive script {primitive_script_path}: {e}")
    sys.exit(1)
finally:
    # Remove the primitive script directory from sys.path
    if primitive_script_dir in sys.path:
        sys.path.remove(primitive_script_dir)

# --- Get current SDF state and process hierarchy (synchronously) ---
# We need to manually gather the state and process the hierarchy since we are not relying on the update manager's async behavior.
current_state = {
    'bounds_name': bounds_obj.name,
    'scene_settings': {key: bounds_obj.get(key, constants.DEFAULT_SETTINGS[key]) for key in constants.DEFAULT_SETTINGS},
    'bounds_matrix': bounds_obj.matrix_world.copy(),
    'source_objects': {},
    'canvas_objects': {},
    'group_objects': {}
}

# Populate source_objects, canvas_objects, group_objects from the scene
# This is a simplified version; a more robust solution would traverse the hierarchy
# and collect all SDF-related objects.
for obj in bpy.context.scene.objects:
    if utils.is_sdf_source(obj) and utils.find_parent_bounds(obj) == bounds_obj:
        props_to_track = {}
        for key, default_val in constants.DEFAULT_SOURCE_SETTINGS.items():
            value = utils.get_sdf_param(obj, key, default_val)
            if isinstance(default_val, float): # Round if default is a float
                value = round(float(value), 5)
            props_to_track[key] = value
        current_state['source_objects'][obj.name] = {'matrix': obj.matrix_world.copy(), 'props': props_to_track}
    elif utils.is_sdf_group(obj) and utils.find_parent_bounds(obj) == bounds_obj:
        props_to_track_group = {}
        for key, default_val in constants.DEFAULT_GROUP_SETTINGS.items():
            value = utils.get_sdf_param(obj, key, default_val)
            if isinstance(default_val, float):
                value = round(float(value), 5)
            props_to_track_group[key] = value
        current_state['group_objects'][obj.name] = {'matrix': obj.matrix_world.copy(), 'props': props_to_track_group}
    elif utils.is_sdf_canvas(obj) and utils.find_parent_bounds(obj) == bounds_obj:
        props_to_track_canvas = {}
        for key, default_val in constants.DEFAULT_CANVAS_SETTINGS.items():
            value = utils.get_sdf_param(obj, key, default_val)
            if isinstance(default_val, float):
                value = round(float(value), 5)
            props_to_track_canvas[key] = value
        current_state['canvas_objects'][obj.name] = {'matrix': obj.matrix_world.copy(), 'props': props_to_track_canvas}

print("Attempting to process SDF hierarchy...")
final_combined_shape = sdf_logic.process_sdf_hierarchy(bounds_obj, current_state['scene_settings'])

if final_combined_shape is None or final_combined_shape is lf.emptiness():
    print("Failed to generate combined SDF shape.")
    sys.exit(1)

print("SDF shape generated. Attempting to get mesh...")

# Define mesh arguments based on bounds object
local_corners = [Vector(c) for c in ((-1,-1,-1), (1,-1,-1), (-1,1,-1), (1,1,-1), (-1,-1,1), (1,-1,1), (1,1,1), (1,1,1))]
world_corners = [(bounds_obj.matrix_world @ c.to_4d()).xyz for c in local_corners]

mesh_args = {
    'xyz_min': (min(c.x for c in world_corners), min(c.y for c in world_corners), min(c.z for c in world_corners)),
    'xyz_max': (max(c.x for c in world_corners), max(c.y for c in world_corners), max(c.z for c in world_corners)),
    'resolution': current_state['scene_settings'].get("sdf_viewport_resolution", 10)
}

mesh_data = None
try:
    mesh_data = final_combined_shape.get_mesh(**mesh_args)
    print("Mesh data generated from SDF shape.")
except Exception as e:
    print(f"Error generating mesh from SDF shape: {e}")
    sys.exit(1)

# --- Create Blender mesh object and populate it ---
result_obj_name = bounds_obj.get(constants.SDF_RESULT_OBJ_NAME_PROP)
result_mesh = bpy.data.meshes.new(result_obj_name + "_Mesh")
result_obj = bpy.data.objects.new(result_obj_name, result_mesh)
bpy.context.collection.objects.link(result_obj)

if mesh_data and mesh_data[0]: # mesh_data is (vertices, triangles)
    result_mesh.from_pydata(mesh_data[0], [], mesh_data[1])
    result_mesh.update()

    # Ensure the object is active and selected for bmesh operations and export
    bpy.ops.object.select_all(action='DESELECT')
    result_obj.select_set(True)
    bpy.context.view_layer.objects.active = result_obj

    # Apply smooth shading to all polygons
    for poly in result_mesh.polygons:
        poly.use_smooth = True

    # Triangulate the mesh using bmesh for consistent export
    bm = bmesh.new()
    bm.from_mesh(result_mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces, quad_method='BEAUTY', ngon_method='BEAUTY')
    bm.to_mesh(result_mesh)
    bm.free()

    print("Blender mesh created and populated.")
else:
    print("No mesh data to populate Blender object.")
    sys.exit(1)

# --- Save .blend file ---
try:
    bpy.data.libraries.write(output_blend_file, {bpy.context.scene})
    print(f"Saved .blend file to: {output_blend_file}")
except Exception as e:
    print(f"Error saving .blend file: {e}")
    sys.exit(1)

# --- Export .obj file ---
try:
    bpy.ops.wm.obj_export(
        filepath=output_obj_file,
        check_existing=True,
        filter_blender=False,
        filter_backup=False,
        filter_image=False,
        filter_movie=False,
        filter_python=False,
        filter_font=False,
        filter_sound=False,
        filter_text=False,
        filter_archive=False,
        filter_btx=False,
        filter_collada=False,
        filter_alembic=False,
        filter_usd=False,
        filter_obj=False,
        filter_volume=False,
        filter_folder=True,
        filter_blenlib=False,
        filemode=8,
        display_type='DEFAULT',
        sort_method='DEFAULT',
        export_animation=False,
        start_frame=0,
        end_frame=1,
        forward_axis='NEGATIVE_Z',
        up_axis='Y',
        global_scale=1,
        apply_modifiers=True,
        export_eval_mode='DAG_EVAL_VIEWPORT',
        export_selected_objects=True,
        export_uv=True,
        export_normals=True,
        export_colors=False,
        export_materials=True,
        export_pbr_extensions=False,
        path_mode='AUTO',
        export_triangulated_mesh=False, # Triangulated by bmesh already
        export_curves_as_nurbs=False,
        export_object_groups=False,
        export_material_groups=False,
        export_vertex_groups=False,
        export_smooth_groups=False,
        smooth_group_bitflags=False,
        filter_glob="*.obj;*.mtl"
    )
    print(f"Exported .obj file to: {output_obj_file}")
except Exception as e:
    print(f"Error exporting .obj file using operator: {e}")
    sys.exit(1)

# --- OBJ Mesh Comparison ---
def read_obj_file(filepath):
    vertices = []
    faces = []
    try:
        with open(filepath, 'r') as f:
            for line in f:
                if line.startswith('v '):
                    parts = line.split()
                    vertices.append(tuple(map(float, parts[1:4])))
                elif line.startswith('f '):
                    parts = line.split()
                    # OBJ faces can be 1-indexed and include normals/textures, simplify to just vertex indices
                    face_indices = []
                    for p in parts[1:]:
                        face_indices.append(int(p.split('/')[0]) - 1) # Convert to 0-indexed
                    faces.append(tuple(face_indices))
        return vertices, faces
    except FileNotFoundError:
        return None, None
    except Exception as e:
        print(f"Error reading OBJ file {filepath}: {e}")
        sys.exit(1)

def compare_obj_meshes(mesh1_data, mesh2_data, tolerance=1e-6):
    if mesh1_data is None or mesh2_data is None:
        return False, "One or both mesh data are None.", False

    vertices1, faces1 = mesh1_data
    vertices2, faces2 = mesh2_data

    if len(vertices1) != len(vertices2):
        return False, f"Vertex count mismatch: {len(vertices1)} vs {len(vertices2)}", False
    if len(faces1) != len(faces2):
        print(f"{len(faces1)}  {len(faces2)}", file=sys.stderr)
        return False, f"Face count mismatch: {len(faces1)} vs {len(faces2)}", True # Indicate face mismatch

    # Compare vertices (with tolerance for float precision)
    for v1, v2 in zip(sorted(vertices1), sorted(vertices2)):
        for i in range(3):
            if abs(v1[i] - v2[i]) > tolerance:
                return False, f"Vertex mismatch: {v1} vs {v2}", False

    return True, "Meshes are identical.", False

# Perform comparison
ground_truth_vertices, ground_truth_faces = read_obj_file(ground_truth_obj_file)
generated_vertices, generated_faces = read_obj_file(output_obj_file)

if ground_truth_vertices is None or ground_truth_faces is None:
    print(f"Test Result: Ground truth file missing or unreadable: {ground_truth_obj_file}", file=sys.stderr)
else:
    are_same, message, face_mismatch_detected = compare_obj_meshes(
        (generated_vertices, generated_faces),
        (ground_truth_vertices, ground_truth_faces)
    )
    base_name = os.path.basename(output_obj_file)
    name_without_extension, _ = os.path.splitext(base_name)
    formatted_filename_ospath = name_without_extension.replace("_test", "")
    capitalized_filename = formatted_filename_ospath.upper()
    if are_same:
        print(f"Test Result: {capitalized_filename} matches ground truth.", file=sys.stderr)
    else:
        print(f"Test Result: {capitalized_filename} does NOT match ground truth. Reason: {message}", file=sys.stderr)
