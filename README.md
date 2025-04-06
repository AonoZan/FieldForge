# FieldForge - Dynamic SDF Modeling for Blender

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Blender Version](https://img.shields.io/badge/Blender-4.1+-orange.svg)](https://www.blender.org)
[![Powered by libfive](https://img.shields.io/badge/Powered%20by-libfive-blue)](https://libfive.com/)
[![Uses libfivepy](https://img.shields.io/badge/Uses-libfivepy-blue)](https://gitlab.com/rcmz0/libfivepy)


FieldForge brings the power of Signed Distance Function (SDF) modeling directly into Blender, leveraging the robust **libfive** library via the **libfivepy** Python bindings. Create complex, non-destructive shapes with hierarchical control and smooth blending capabilities.

![FieldForge Demo GIF](Screenshot.png)

## Features

*   **Non-Destructive Workflow:** Build complex models using mathematical functions without relying on traditional mesh topology.
*   **Libfive Powered:** Utilizes the fast and reliable libfive engine for SDF calculations and meshing.
*   **Hierarchical Control:** Parent SDF source objects (Empties) to create complex relationships.
*   **Smooth Blending:** Control the smoothness of unions and differences between shapes, both globally (per system) and per-parent.
*   **Primitive Shapes:** Add pre-defined SDF sources:
    *   Cube
    *   Sphere
    *   Cylinder
    *   Cone
    *   Torus
    *   Rounded Box (with adjustable radius)
*   **Subtractive Modeling:** Mark shapes as 'Negative' to perform boolean differences.
*   **Per-System Bounds:** Define independent SDF systems using "Bounds Controller" objects.
*   **Configurable Resolution:** Set separate mesh resolutions for viewport previews and final renders/manual updates.
*   **Performance Controls:**
    *   Adjustable viewport update delay (debouncing).
    *   Minimum update interval (throttling).
    *   Option to disable automatic viewport updates.
*   **Integrated UI:**
    *   Add menu (`View3D > Add > Mesh > Field Forge SDF`).
    *   Dedicated panels in the Object Properties tab for Bounds Controllers and SDF Sources.

## Dependencies

FieldForge relies on external components that **must be included** with the addon files:

1.  **Blender:** Version 4.1 or newer.
2.  **libfivepy:** Python bindings for libfive.
    *   GitLab Repository: [https://gitlab.com/rcmz0/libfivepy](https://gitlab.com/rcmz0/libfivepy)
3.  **Compiled libfive Libraries:** The core libfive C++ library (`libfive.*`) and its standard library (`libfive-stdlib.*`) compiled for your operating system (Windows `.dll`, macOS `.dylib`, Linux `.so`). These are **required** by `libfivepy`.

## Installation

**IMPORTANT:** Due to the requirement for pre-compiled libraries, FieldForge cannot be installed *only* from its Python source code. You need the complete package including the necessary library files.

**Manual Method (From Source):**

This method is generally for developers or if a pre-packaged release is unavailable.

1.  **Clone or Download:** Get the FieldForge source code (this repository).
2.  **Obtain libfivepy & Compiled Libraries:**
    *   You need `libfivepy` and, more importantly, the *compiled* libfive core and stdlib libraries (`.dll`/`.so`/`.dylib`) compatible with `libfivepy` and your operating system.
    *   Refer to the [libfivepy documentation](https://gitlab.com/rcmz0/libfivepy) or the [libfive project](https://libfive.com/) for instructions on obtaining or building these libraries.
    *   **Crucially, you need the binary files, not just the source code.**
3.  **Arrange Files:** Place the downloaded/compiled libraries into the correct subdirectories within the FieldForge addon folder, matching this structure *exactly*:
    ```
    FieldForge/
    ├── __init__.py
    └── libfive/
        ├── __init__.py
        ├── ffi.py
        ├── runner.py
        ├── shape.py
        ├── src/
        │   ├── libfive.dll         # Windows Core
        │   ├── libfive.dylib       # macOS Core
        │   ├── libfive.so          # Linux Core
        │   ├── libpng16.dll      # Windows Dependency (Example)
        │   └── zlib1.dll         # Windows Dependency (Example)
        └── stdlib/
            ├── __init__.py
            ├── csg.py
            ├── libfive-stdlib.dll  # Windows Stdlib
            ├── libfive-stdlib.dylib # macOS Stdlib
            ├── libfive-stdlib.so   # Linux Stdlib
            ├── shapes.py
            ├── text.py
            └── transforms.py
    ```
    *(Note: Dependency files like `libpng` and `zlib` might be needed on Windows, depending on how libfive was compiled).*
4.  **Zip the Addon:** Create a zip archive containing *only* the `__init__.py` file and the `libfive` folder (with all its contents correctly placed). The structure inside the zip should mirror the layout above.
5.  **Install in Blender:** Follow steps 2-5 from the "Recommended Method" above, using the zip file you created.

## Basic Usage

1.  **Add Bounds:** Go to `View3D > Add > Mesh > Field Forge SDF > Add Bounds Controller`. This creates an Empty object that defines the region and settings for an SDF system.
2.  **Select Bounds:** Select the newly created Bounds Controller (`SDF_System_Bounds` by default).
3.  **Add Sources:** With the Bounds (or another SDF Source Empty) selected, go to `View3D > Add > Mesh > Field Forge SDF` and choose a source shape (e.g., `SDF Cube Source`). The new source Empty will be parented to the active object.
4.  **Transform Sources:** Move, rotate, and scale the source Empties to position and size the underlying SDF shapes.
5.  **Adjust Settings:**
    *   Select the **Bounds Controller** and go to the `Object Properties` tab. The "FieldForge Bounds Settings" panel lets you control resolution, update timing, global blend factors, etc.
    *   Select an **SDF Source Empty** and go to the `Object Properties` tab. The "FieldForge Source Properties" panel allows you to mark it as negative (subtractive), set its *child* blend factor, and adjust type-specific parameters (like rounding radius for Rounded Box).
6.  **Hierarchy:** Parent SDF Sources to each other to build complex forms. The blend factor between siblings is controlled by their *parent's* "Child Blend Factor" property (or the Bounds' "Global Blend Factor" for top-level sources).
7.  **Updates:**
    *   The viewport mesh updates automatically after a short period of inactivity (if Auto Update is enabled).
    *   Click "Update Final SDF Now" in the Bounds Settings panel for a high-resolution update using the "Final Resolution".

## Troubleshooting

*   **"libfive not available" / Addon fails to enable:** This almost always means the compiled libfive libraries (`.dll`/`.so`/`.dylib`) are missing, in the wrong location, or incompatible with your OS/Blender Python version. Double-check the **Installation** steps and the **File Structure**. Check Blender's system console (`Window > Toggle System Console`) for specific error messages during startup.
*   **Slow Performance:**
    *   Lower the "Viewport Resolution" in the Bounds Settings.
    *   Increase the "Viewport Inactivity Delay" or "Min Update Interval".
    *   Disable "Auto Viewport Update" and use the "Update Final SDF Now" button manually.
    *   Simplify your SDF hierarchy if possible.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests. [TODO: Add contributing guidelines if desired].

## License

FieldForge is licensed under the **GNU General Public License v3.0**. See the [GPLv3 License](https://www.gnu.org/licenses/gpl-3.0.en.html) for details.

## Acknowledgements

*   **libfive:** The core SDF library. [https://libfive.com/](https://libfive.com/)
*   **libfivepy:** Python bindings for libfive. [https://gitlab.com/rcmz0/libfivepy](https://gitlab.com/rcmz0/libfivepy)
