#!/bin/bash

# This script sets the ground truth for generated OBJ files.
# It can either process a single specified OBJ file or traverse all subfolders
# within the 'tests' directory to find and set ground truths for all generated OBJ files.
# Usage:
#   To set ground truth for a single file: ./set_ground_truth.sh <relative_path_to_generated_obj_from_tests_dir>
#   To set ground truth for all generated OBJs: ./set_ground_truth.sh

TESTS_DIR="$(dirname "$(realpath "$0")")"

set_single_ground_truth() {
    local GENERATED_OBJ_RELATIVE="$1"
    local GENERATED_OBJ_FULL_PATH="${TESTS_DIR}/${GENERATED_OBJ_RELATIVE}"

    if [ ! -f "$GENERATED_OBJ_FULL_PATH" ]; then
        echo "Error: Generated OBJ file not found at '$GENERATED_OBJ_FULL_PATH'"
        return 1
    fi

    local DIR=$(dirname "$GENERATED_OBJ_FULL_PATH")
    local FILENAME=$(basename "$GENERATED_OBJ_FULL_PATH" .obj)
    local GROUND_TRUTH_OBJ="${DIR}/${FILENAME}_ground_truth.obj"

    echo "Setting ground truth for '$GENERATED_OBJ_FULL_PATH'..."
    cp -f "$GENERATED_OBJ_FULL_PATH" "$GROUND_TRUTH_OBJ"

    if [ $? -eq 0 ]; then
        echo "Ground truth set: '$GROUND_TRUTH_OBJ'"
    else
        echo "Error: Failed to set ground truth for '$GENERATED_OBJ_FULL_PATH'."
        return 1
    fi
    return 0
}

if [ -n "$1" ]; then
    # Single file mode
    set_single_ground_truth "$1"
else
    # Traverse subfolders mode
    echo "Setting ground truth for all generated OBJ files in subfolders of '${TESTS_DIR}'..."
    find "${TESTS_DIR}" -type f -name "*.obj" ! -name "*_ground_truth.obj" | while read -r obj_file;
    do
        # Get path relative to TESTS_DIR
        RELATIVE_PATH="${obj_file#${TESTS_DIR}/}"
        set_single_ground_truth "$RELATIVE_PATH"
    done
    echo "Ground truth setting complete."
fi
