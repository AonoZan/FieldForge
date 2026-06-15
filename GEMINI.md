# FieldForge - Gemini Development Guide

This document provides an overview of the FieldForge Blender addon for the Gemini AI agent, outlining its purpose, structure, and key development considerations.

## 1. Project Description

FieldForge is a Blender addon that integrates Signed Distance Function (SDF) modeling using the `libfive` library. It enables non-destructive, procedural 3D modeling directly within Blender 5.0+, offering a hierarchical workflow, various primitive shapes, CSG operations, and modifiers.

## 2. Key Technologies

*   **Blender:** The 3D creation suite hosting the addon (designed for Blender 5.0+).
*   **Python:** The primary language for Blender addons and FieldForge's scripting.
*   **libfive:** A powerful C++ library for SDFs, integrated via Python bindings.

## 3. Project Structure

The project is organized as follows:

*   `__init__.py`: Blender addon registration and main entry point.
*   `blender_manifest.toml`: Metadata manifest file for Blender 4.2+ extension platform compatibility.
*   `constants.py`: Defines various constants and default properties used throughout the addon.
*   `drawing.py`: Handles custom viewport drawing of boundaries and wireframes using Blender's modern `gpu` API.
*   `utils.py`: Contains general utility functions.
*   `core/`: Core logic of the SDF system, including handlers, SDF logic, state management, and update mechanisms.
*   `libfive/`: Contains the `libfive` library and its Python bindings (expected compiled binaries go under `libfive/src/`).
*   `ui/`: Defines the user interface elements, including menus, operators, and panels.
*   `tests/` / `test_sdf.py`: Testing scripts and suites for verifying SDF math and system behavior.
*   `.github/workflows/`: Contains GitHub Actions for release automation (Linux, macOS, Windows).

## 4. Development Environment Setup

To set up a development environment for FieldForge:

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/AonoZan/FieldForge.git
    cd FieldForge
    ```
2.  **Blender Installation:** Ensure you have Blender 5.0 or newer installed.
3.  **Libfive:** The `libfive` library and its Python bindings are pre-compiled and included in release packages. For development, ensure that compatible `libfive` shared libraries are placed in the `libfive/src/` directory.
4.  **Blender Addon Setup:**
    *   Since FieldForge uses Blender's modern Extension system, you can link the development directory or install the addon by packing it or setting up a developer repository path in Blender's Preferences (`Edit > Preferences > Get Extensions > Developer Settings`).

## 5. Building libfive from Source

Because pre-compiled binaries are used in release ZIP packages, the `libfive_source` directory is no longer bundled directly in the main repository. To build `libfive` from source for custom development or troubleshooting:

1.  **Obtain the Source:** Clone the official library repository from [libfive GitHub repository](https://github.com/libfive/libfive).
2.  **Install Dependencies:** The specific dependencies include a C++ compiler, `cmake`, `Eigen`, `libpng`, and `Boost`.
3.  **Run CMake & Compile:** Configure and build using CMake, then copy the compiled shared library files to `FieldForge/libfive/src/`.

Refer to the official libfive documentation for detailed build procedures across platforms (macOS, Linux, Windows).

## 6. Build/Release Process

The project uses GitHub Actions for its release process, as indicated by the workflows in `.github/workflows/`. These workflows:

*   Build and compile `libfive` binaries across platforms.
*   Package the addon files, including the metadata `blender_manifest.toml` and compiled `libfive` libraries, into a distributable extension ZIP file.

To package the extension manually:
1.  Ensure all necessary files and `libfive` binaries are present in the `FieldForge` folder.
2.  Compress the directory into a `.zip` file for installation or distribution.

## 7. Libfive Rendering Optimizations

FieldForge employs several key optimizations to ensure a smooth and interactive user experience:

*   **Asynchronous Meshing:** The conversion of the mathematical SDF representation into a Blender triangle mesh is offloaded to a background thread to prevent the Blender UI from freezing.
*   **Adaptive Resolution and Progressive Rendering:** Viewport resolution dynamically adapts based on meshing performance. If meshing is slow, the resolution is temporarily decreased to maintain responsiveness, and refines when the user pauses.
*   **Parallel Evaluation:** The `libfive` library utilizes multi-core processing to calculate the SDF and generate the mesh in parallel.
*   **Custom Viewport Drawing:** Instead of heavy mesh generation for every minor view step, boundaries and wireframe outlines are drawn efficiently in the viewport using Blender's modern `gpu` shader API.
*   **Tree Optimization (Common Subexpression Elimination):** The application canonicalizes SDF trees to identify identical sub-trees, ensuring duplicate parts are computed and stored efficiently.
