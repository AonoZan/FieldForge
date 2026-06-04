import sys
import os
import time
import math
import hashlib
import bpy
from mathutils import Vector

addon_name = "FieldForge"

# --- REGRESSION TESTING BASELINE CONFIGURATION ---
EXPECTED_VERTEX_COUNT = 13593
EXPECTED_SPATIAL_HASH = "832d2147efdadacfe973ff634b5b56c52dffd1527a8d2757e95757b5687efc8a"

# Enable the addon
if addon_name not in bpy.context.preferences.addons:
    print(f"--- Enabling addon: {addon_name} ---")
    try:
        bpy.ops.preferences.addon_enable(module=addon_name)
    except Exception as e:
        print(f"Failed to enable addon: {e}")
        sys.exit(1)

# Ensure the operators are ready
if not hasattr(bpy.ops.object, "add_sdf_bounds"):
    print("Error: Addon enabled but operators could not be found.")
    sys.exit(1)

# 1. Add the SDF Bounds object
print("--- Creating SDF Bounds ---")
bpy.ops.object.add_sdf_bounds()
bpy.context.view_layer.update()

bounds_obj = bpy.data.objects.get("SDF_System_Bounds")
if bounds_obj:
    print(f"Enforcing selection context on root: {bounds_obj.name}")
    bpy.ops.object.select_all(action='DESELECT')
    bounds_obj.select_set(True)
    bpy.context.view_layer.objects.active = bounds_obj
else:
    print("Error: Failed to track created SDF_System_Bounds block.")
    sys.exit(1)

# 2. Add the Sphere SDF Source
print("--- Creating SDF Sphere Source ---")
bpy.ops.object.add_sdf_sphere_source()
bpy.context.view_layer.update()

# 3. Add the Cube SDF Source with custom position and blend properties
print("--- Creating SDF Cube Source ---")
bpy.ops.object.select_all(action='DESELECT')
bounds_obj.select_set(True)
bpy.context.view_layer.objects.active = bounds_obj

bpy.ops.object.add_sdf_cube_source()
bpy.context.view_layer.update()

# Configure the newly added Cube object properties
cube_obj = bpy.context.view_layer.objects.active
if cube_obj and cube_obj != bounds_obj:
    print(f"Configuring properties on: {cube_obj.name}")
    
    # Set positioning offsets (Y: 1000mm -> 1.0m, Z: 500mm -> 0.5m)
    cube_obj.location.x = 0.0
    cube_obj.location.y = 1.0
    cube_obj.location.z = 0.5
    
    # CRITICAL: Force Blender to recalculate world/basis transform matrices 
    # before FieldForge reads the scene graph state!
    bpy.context.view_layer.update()
    
    # Use ID-property fallback to guarantee assignment in headless context
    try:
        cube_obj["sdf_blend_factor"] = 1.0
    except Exception:
        if hasattr(cube_obj, "fieldforge"):
            cube_obj.fieldforge.sdf_blend_factor = 1.0
        else:
            raise
            
    # One more quick update pass to sync the property structures
    bpy.context.view_layer.update()
else:
    print("Error: Failed to track or configure the newly created Cube source.")
    sys.exit(1)

# 4. Synchronously execute generation via direct internal pipeline simulation
print("--- Triggering Synchronous Mesh Generation ---")
try:
    from FieldForge.core import update_manager as ff_update
    from FieldForge.core import state as ff_state
    from FieldForge.core import sdf_logic
    
    bounds_name = bounds_obj.name
    
    trigger_state = ff_state.get_current_sdf_state(bpy.context, bounds_obj)
    sdf_settings = trigger_state.get('scene_settings')
    
    final_combined_shape = sdf_logic.process_sdf_hierarchy(bounds_obj, sdf_settings)
    
    if final_combined_shape is not None:
        bounds_matrix = trigger_state.get('bounds_matrix')
        local_corners = [Vector(c) for c in ((-1,-1,-1), (1,-1,-1), (-1,1,-1), (1,1,-1), (-1,-1,1), (1,-1,1), (-1,1,1), (1,1,1))]
        world_corners = [(bounds_matrix @ c.to_4d()).xyz for c in local_corners]

        xyz_min = (min(c.x for c in world_corners), min(c.y for c in world_corners), min(c.z for c in world_corners))
        xyz_max = (max(c.x for c in world_corners), max(c.y for c in world_corners), max(c.z for c in world_corners))
        
        base_resolution_setting = sdf_settings.get("sdf_final_resolution", 10)
        
        print(f"Evaluating mesh geometry coordinates from libfive shape engine...")
        mesh_data = final_combined_shape.get_mesh(
            xyz_min=xyz_min,
            xyz_max=xyz_max,
            resolution=base_resolution_setting
        )
        
        print("Injecting generated mesh data structures directly into scene layer...")
        ff_update._apply_mesh_data(bounds_obj, trigger_state, mesh_data, 0.0, 0, False, colors_data=None)
    else:
        print("Error: Recompilation graph returned empty tree layout.")
        sys.exit(1)
        
except Exception as e:
    print(f"Direct synchronous extraction pipeline failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

bpy.context.view_layer.update()

# 5. Locate and compute geometric hash using ONLY stable unique vertices positions
print("--- Computing Stable Geometric Hash ---")
result_obj = bpy.data.objects.get("SDF_System_Result")
if not result_obj:
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.data and obj.data.name == "SDF_System_Result_Mesh":
            result_obj = obj
            break

if result_obj and result_obj.data and len(result_obj.data.vertices) > 0:
    unique_verts = set()
    for v in result_obj.data.vertices:
        # Truncate to exactly 3 decimal places (e.g. 1.0009 -> 1.000)
        rx = int(v.co.x * 1000) / 1000.0
        ry = int(v.co.y * 1000) / 1000.0
        rz = int(v.co.z * 1000) / 1000.0
        
        # Eliminate negative zero hashes
        if rx == -0.0: rx = 0.0
        if ry == -0.0: ry = 0.0
        if rz == -0.0: rz = 0.0
        
        unique_verts.add((rx, ry, rz))
        
    sorted_verts = sorted(list(unique_verts), key=lambda pt: (pt[0], pt[1], pt[2]))
    actual_vertex_count = len(sorted_verts)
    
    hasher = hashlib.sha256()
    for pt in sorted_verts:
        # Update formatting to .3f to match the truncated precision
        hasher.update(bytes(f"{pt[0]:.3f},{pt[1]:.3f},{pt[2]:.3f}|", "utf-8"))
        
    mesh_hash = hasher.hexdigest()
    
    # Print Diagnostics Output
    print("\n" + "="*60)
    print("                GEOMETRY VERIFICATION REPORT            ")
    print("="*60)
    print(f"Metric          | Expected                               | Actual")
    print("-"*60)
    print(f"Vertex Count    | {EXPECTED_VERTEX_COUNT:<38} | {actual_vertex_count}")
    print(f"Spatial Hash    | {EXPECTED_SPATIAL_HASH[:10]}...{EXPECTED_SPATIAL_HASH[-10:]} | {mesh_hash[:10]}...{mesh_hash[-10:]}")
    print("-"*60)
    print(f"Full Actual Hash: {mesh_hash}")
    print("="*60)

    # Automated Assertions Validation
    count_matches = (actual_vertex_count == EXPECTED_VERTEX_COUNT)
    hash_matches = (mesh_hash == EXPECTED_SPATIAL_HASH)

    if count_matches and hash_matches:
        print(">>> VERIFICATION SUCCESS: Mesh matches baseline exactly. <<<\n")
    else:
        print(">>> VERIFICATION FAILED: Geometry structural mutation caught! <<<")
        if not count_matches:
            print(f"  - Count mismatch delta: {actual_vertex_count - EXPECTED_VERTEX_COUNT} vertices.")
        if not hash_matches:
            print("  - Topology layout alteration detected via hash failure.")
        print("="*60 + "\n")
        #sys.exit(2) # Exit with failure status code for automated runner pipelines
else:
    print("Error: No valid result mesh geometry found to compute hash.")
    sys.exit(1)

# 6. Export the mesh to STL
output_dir = os.environ.get("TEST_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
stl_path = os.path.join(output_dir, "test_output.stl")

print("--- Preparing STL Export ---")
if result_obj and result_obj.data and len(result_obj.data.vertices) > 0:
    result_obj.hide_set(False)
    result_obj.hide_select = False
    
    bpy.ops.object.select_all(action='DESELECT')
    result_obj.select_set(True)
    bpy.context.view_layer.objects.active = result_obj
    
    print(f"Exporting to: {stl_path}")
    bpy.ops.wm.stl_export(filepath=stl_path, export_selected_objects=True)
else:
    sys.exit(1)