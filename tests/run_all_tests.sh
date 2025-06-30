#!/bin/bash

# Define the path to the generic test scene script
BASE_TEST_SCRIPT="$(dirname "$(realpath "$0")")/base_test_scene.py"

# Define the path to the primitive-specific test script for cube
CUBE_TEST_SCRIPT="$(dirname "$(realpath "$0")")/primitives/cube/test_cube.py"

# Define output paths for the cube test
OUTPUT_BLEND_FILE_CUBE="$(dirname "$(realpath "$0")")/primitives/cube/cube_test.blend"
OUTPUT_OBJ_FILE_CUBE="$(dirname "$(realpath "$0")")/primitives/cube/cube_test.obj"
GROUND_TRUTH_OBJ_FILE_CUBE="$(dirname "$(realpath "$0")")/primitives/cube/cube_test_ground_truth.obj"

# Define the path to the primitive-specific test script for sphere
SPHERE_TEST_SCRIPT="$(dirname "$(realpath "$0")")/primitives/sphere/test_sphere.py"

# Define output paths for the sphere test
OUTPUT_BLEND_FILE_SPHERE="$(dirname "$(realpath "$0")")/primitives/sphere/sphere_test.blend"
OUTPUT_OBJ_FILE_SPHERE="$(dirname "$(realpath "$0")")/primitives/sphere/sphere_test.obj"
GROUND_TRUTH_OBJ_FILE_SPHERE="$(dirname "$(realpath "$0")")/primitives/sphere/sphere_test_ground_truth.obj"

# Define the path to the primitive-specific test script for cylinder
CYLINDER_TEST_SCRIPT="$(dirname "$(realpath "$0")")/primitives/cylinder/test_cylinder.py"

# Define output paths for the cylinder test
OUTPUT_BLEND_FILE_CYLINDER="$(dirname "$(realpath "$0")")/primitives/cylinder/cylinder_test.blend"
OUTPUT_OBJ_FILE_CYLINDER="$(dirname "$(realpath "$0")")/primitives/cylinder/cylinder_test.obj"
GROUND_TRUTH_OBJ_FILE_CYLINDER="$(dirname "$(realpath "$0")")/primitives/cylinder/cylinder_test_ground_truth.obj"

# Check for --verbose argument
VERBOSE=false
for arg in "$@"; do
  if [ "$arg" == "--verbose" ]; then
    VERBOSE=true
    break
  fi
done

# Function to run a test and capture output
run_test() {
  local script="$1"
  local blend_file="$2"
  local obj_file="$3"
  local ground_truth_file="$4"
  local test_name="$5"

  if [ "$VERBOSE" = true ]; then
    echo "Running $test_name..."
    blender --background --factory-startup --python "$BASE_TEST_SCRIPT" -- "$script" "$blend_file" "$obj_file" "$ground_truth_file"
  else
    # Capture stderr for test results, discard stdout
    blender --background --factory-startup --python "$BASE_TEST_SCRIPT" -- "$script" "$blend_file" "$obj_file" "$ground_truth_file" 2> >(grep "Test Result:") >/dev/null
  fi
}

# --- Run Cube Primitive Test ---
run_test "$CUBE_TEST_SCRIPT" "$OUTPUT_BLEND_FILE_CUBE" "$OUTPUT_OBJ_FILE_CUBE" "$GROUND_TRUTH_OBJ_FILE_CUBE" "Cube Primitive Test"

# --- Run Sphere Primitive Test ---
run_test "$SPHERE_TEST_SCRIPT" "$OUTPUT_BLEND_FILE_SPHERE" "$OUTPUT_OBJ_FILE_SPHERE" "$GROUND_TRUTH_OBJ_FILE_SPHERE" "Sphere Primitive Test"

# --- Run Cylinder Primitive Test ---
run_test "$CYLINDER_TEST_SCRIPT" "$OUTPUT_BLEND_FILE_CYLINDER" "$OUTPUT_OBJ_FILE_CYLINDER" "$GROUND_TRUTH_OBJ_FILE_CYLINDER" "Cylinder Primitive Test"

