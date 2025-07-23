# FieldForge - Gemini Development Guide

This document provides an overview of the FieldForge Blender addon for the Gemini AI agent, outlining its purpose, structure, and key development considerations.

## 1. Project Description

FieldForge is a Blender addon that integrates Signed Distance Function (SDF) modeling using the `libfive` library. It enables non-destructive, procedural 3D modeling directly within Blender, offering a hierarchical workflow, various primitive shapes, CSG operations, and modifiers.

## 2. Key Technologies

*   **Blender:** The 3D creation suite that hosts the addon.
*   **Python:** The primary language for Blender addons and FieldForge's scripting.
*   **libfive:** A powerful C++ library for SDFs, integrated via Python bindings.

## 3. Project Structure

The project is organized as follows:

*   `__init__.py`: Blender addon registration and main entry point.
*   `constants.py`: Defines various constants used throughout the addon.
*   `drawing.py`: Handles custom drawing in the Blender viewport.
*   `utils.py`: Contains utility functions.
*   `core/`: Core logic of the SDF system, including handlers, SDF logic, state management, and update mechanisms.
*   `libfive/`: Contains the `libfive` library and its Python bindings. This is crucial for the addon's functionality.
*   `libfive_source/`: Contains the source code for the `libfive` library. This is useful for reference and for building the library from scratch.
*   `ui/`: Defines the user interface elements, including menus, operators, and panels.
*   `.github/workflows/`: Contains GitHub Actions for release automation (Linux, macOS, Windows).

## 4. Development Environment Setup

To set up a development environment for FieldForge:

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/AonoZan/FieldForge.git
    cd FieldForge
    ```
2.  **Blender Installation:** Ensure you have Blender 4.1 or newer installed.
3.  **Libfive:** The `libfive` library and its Python bindings are pre-compiled and included in the release ZIP. For development, ensure your environment can correctly access these. If developing on a new system, you might need to ensure the `libfive` binaries are compatible or recompile them for your specific OS.
4.  **Blender Addon Setup:**
    *   Open Blender.
    *   Go to `Edit > Preferences > Add-ons`.
    *   Click `Install...` and navigate to the cloned `FieldForge` directory. Select the `__init__.py` file (or the entire directory if Blender allows).
    *   Enable the "FieldForge" addon.

## 5. Building libfive from Source

The `libfive_source` directory contains the complete source code for the `libfive` library, which is the core of FieldForge's SDF functionality. This allows for advanced customization, debugging, and building for unsupported platforms.

To build `libfive` from source, you will need to:

1.  **Install Dependencies:** The specific dependencies vary by operating system but generally include a C++ compiler, `cmake`, `Eigen`, `libpng`, and `Boost`. For detailed lists, refer to the `libfive_source/README.md` file.
2.  **Run CMake:** Use `cmake` to configure the build system.
3.  **Compile:** Use `make` or your platform's equivalent to compile the source code.

For complete, step-by-step instructions for macOS, Linux, and Windows, please see the [libfive README file](libfive_source/README.md).


## 6. Build/Release Process

The project uses GitHub Actions for its release process, as indicated by the workflows in `.github/workflows/`. These workflows are responsible for:

*   `release-linux.yml`
*   `release-macos.yml`
*   `release-windows.yml`

These likely handle packaging the addon, including the pre-compiled `libfive` libraries, into a distributable ZIP file for each operating system. To create a release package manually, you would typically:

1.  Ensure all necessary files, including the `libfive` binaries for the target OS, are correctly placed within the `FieldForge` directory.
2.  Zip the entire `FieldForge` directory. The resulting `.zip` file is what users install directly in Blender.

## 7. Libfive Rendering Optimizations

The `libfive` library and the Studio application employ several key optimizations to ensure a smooth and interactive user experience:

*   **Asynchronous Meshing:** The conversion of the mathematical SDF representation into a renderable triangle mesh is performed in a background thread. This prevents the UI from freezing while complex meshes are being generated, ensuring the application remains responsive.
*   **Adaptive Resolution and Progressive Rendering:** The application dynamically adjusts the mesh quality based on the time it takes to generate the mesh. If meshing is fast, the resolution is increased for more detail. If it's slow, the resolution is decreased to maintain interactivity. This provides a fast, low-resolution preview during active editing, which refines into a high-quality version when the user pauses.
*   **Parallel Evaluation:** The `libfive` library is designed to leverage multi-core processors. It uses a pool of evaluators to calculate the SDF and generate the mesh in parallel, significantly speeding up the process.
*   **Efficient OpenGL Rendering:** Once a mesh is generated, it is rendered using modern OpenGL techniques. Vertex Buffer Objects (VBOs) and Vertex Array Objects (VAOs) are used to store the mesh data on the GPU, allowing for high-performance rendering with minimal CPU overhead.
*   **Tree Optimization (Common Subexpression Elimination):** The application analyzes the SDF trees of all shapes and identifies identical sub-trees. This process, called "co-optimization" or "canonicalization," ensures that if you have multiple copies of the same object, the underlying SDF is only stored and meshed once, saving memory and computation time.
