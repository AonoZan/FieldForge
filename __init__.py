# FieldForge/__init__.py

bl_info = {
    "name": "FieldForge",
    "author": "Your Name & libfive Team",
    "version": (0, 5, 3), # Consider fetching from constants.py if desired
    "blender": (4, 4, 0), # Make sure this matches the Blender version you target
    "location": "View3D > Sidebar (N-Panel) > FieldForge Tab | Add > Mesh > Field Forge SDF",
    "description": "Adds and manages dynamic SDF shapes using libfive with hierarchical blending, extrusion, and custom visuals",
    "warning": "Requires compiled libfive libraries.",
    "doc_url": "", # Add your documentation link here
    "category": "Add Mesh",
}

import bpy
import os
import sys
import traceback # For error reporting

# --- Libfive Loading and Setup ---
# This setup logic remains crucial here at the entry point.

addon_dir = os.path.dirname(os.path.realpath(__file__))
# Assuming libfive python wrapper is directly in addon_dir or a subdir detectable from there
libfive_python_dir = addon_dir
if libfive_python_dir not in sys.path:
    sys.path.append(libfive_python_dir)

# Path to the actual compiled libraries (assuming a specific structure)
# Adjust this path if your bundled library structure is different
libfive_base_dir = os.path.join(addon_dir, 'libfive', 'src') # Expected location of ffi.py etc.
libfive_lib_dir = os.path.join(addon_dir, 'libfive') # Parent for 'src' and 'stdlib'

# print(f"FieldForge: Addon Dir = {addon_dir}")
# print(f"FieldForge: Calculated Libfive Lib Dir = {libfive_lib_dir}")

# Set environment variable *before* ffi import (if needed by your ffi.py)
# This tells ffi.py where to look for the compiled libs (.so, .dll, .dylib)
if os.path.isdir(libfive_lib_dir):
    os.environ['LIBFIVE_FRAMEWORK_DIR'] = libfive_base_dir
    print(f"FieldForge: Set LIBFIVE_FRAMEWORK_DIR='{libfive_lib_dir}'")
else:
    print(f"FieldForge: Warning - Libfive library directory not found at: {libfive_lib_dir}")
    # Consider checking other potential locations or relying solely on system paths

libfive_available = False
lf = None
ffi = None
try:
    # Import libfive components (assuming they are importable after path setup)
    print("FieldForge: Attempting libfive imports...")
    import libfive.ffi as ffi
    import libfive.shape # Import shape early if stdlib depends on it
    import libfive.stdlib as lf

    # Basic check for successful loading
    if hasattr(lf, 'sphere') and hasattr(ffi.lib, 'libfive_tree_const'):
        libfive_available = True
        print("FieldForge: Successfully imported and verified libfive.")
    else:
         print("FieldForge: Libfive imported but core function check failed.")
         raise ImportError("Core function check failed")

except ImportError as e:
    # print(f"FieldForge: Error importing libfive: {e}")
    # Provide guidance based on expected structure
    core_lib_path = os.path.join(libfive_lib_dir, "src", f"libfive.{'dll' if sys.platform == 'win32' else 'dylib' if sys.platform == 'darwin' else 'so'}")
    stdlib_lib_path = os.path.join(libfive_lib_dir, "stdlib", f"libfive-stdlib.{'dll' if sys.platform == 'win32' else 'dylib' if sys.platform == 'darwin' else 'so'}")
    # print(f"FieldForge: Ensure compiled libraries exist relative to addon, e.g.:")
    # print(f"  - Core: {core_lib_path}")
    # print(f"  - Stdlib: {stdlib_lib_path}")
    current_env_var = os.environ.get('LIBFIVE_FRAMEWORK_DIR', '<Not Set>')
    # print(f"FieldForge: Current LIBFIVE_FRAMEWORK_DIR='{current_env_var}'")
    print("FieldForge: Addon requires libfive. Dynamic functionality disabled.")
except Exception as e:
    # print(f"FieldForge: An unexpected error occurred during libfive import: {type(e).__name__}: {e}")
    traceback.print_exc()
    print("FieldForge: Dynamic functionality disabled.")

# --- Module Imports (Relative) ---
# Use a structure that allows reloading during development if needed
if "bpy" in locals():
    import importlib
    # Order can matter if modules depend on each other
    from . import constants
    from . import utils
    from .core import sdf_logic
    from .core import state
    from .core import update_manager
    from . import drawing
    from .ui import operators
    from .ui import panels
    from .ui import menus

    importlib.reload(constants)
    importlib.reload(utils)
    importlib.reload(sdf_logic)
    importlib.reload(state)
    importlib.reload(update_manager)
    importlib.reload(drawing)
    importlib.reload(operators)
    importlib.reload(panels)
    importlib.reload(menus)
else:
    # Standard imports for first load
    from . import constants
    from . import utils
    from .core import sdf_logic
    from .core import state
    from .core import update_manager
    from . import drawing
    from .ui import operators
    from .ui import panels
    from .ui import menus

# Make libfive accessible to other modules if needed (alternative is passing it)
# This depends on how you structure dependencies. If sdf_logic, etc., import
# lf directly from libfive, this might not be needed. If they expect it passed
# or imported from the root __init__, keep these lines.
if libfive_available:
    utils.lf = lf
    utils.ffi = ffi
    sdf_logic.lf = lf
    sdf_logic.ffi = ffi
    # Add lf/ffi to other modules like drawing, state, update_manager if they use it directly

# --- Collect Classes ---
# Assumes each module defines a tuple/list named 'classes_to_register'
classes_to_register = (
    *operators.classes_to_register,
    *panels.classes_to_register,
    *menus.classes_to_register,
    # Add other classes here if defined directly in other modules
)

# --- Registration ---
def register():
    # print(f"\nRegistering {bl_info['name']} addon...")

    if not libfive_available:
        print("FieldForge: Libfive not available, registration incomplete.")
        # Optionally register a panel showing an error message
        # bpy.utils.register_class(ErrorPanelClass) # Define this class if needed
        return

    # Register Properties (if you add PropertyGroups later)
    # bpy.utils.register_class(MyPropertyGroup)
    # bpy.types.Object.my_prop_group = PointerProperty(type=MyPropertyGroup)

    # Register Classes
    print("FieldForge: Registering classes...")
    for cls in classes_to_register:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            # print(f"  - Class {cls.__name__} already registered?") # Should not happen on clean load
            pass
        except Exception as e:
            # print(f"  ERROR: Failed to register class {cls.__name__}: {e}")
            traceback.print_exc()

    # Add Menu Items
    print("FieldForge: Adding menus...")
    try:
        bpy.types.VIEW3D_MT_add.append(menus.menu_func)
    except Exception as e:
        print(f"  ERROR: Could not add menu item: {e}")

    # Register Handlers
    print("FieldForge: Registering handlers...")
    try:
        # Depsgraph Handler
        if update_manager.ff_depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(update_manager.ff_depsgraph_handler)

        # Draw Handler (store handle within drawing module)
        if drawing._draw_handle is None:
            drawing._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
                drawing.ff_draw_callback, (), 'WINDOW', 'POST_VIEW'
            )
            print("  - Custom Draw Handler registered.")
        else:
             print("  - Custom Draw Handler already registered?")
    except Exception as e:
        print(f"  ERROR: Failed registering handlers: {e}")

    # Start Modal Operator (using function in operators module)
    print("FieldForge: Starting modal select handler...")
    try:
        # Use a timer to ensure Blender UI is ready
        # Assumes operators.py defines a function like 'start_select_handler_via_timer'
        operators.start_select_handler_via_timer()
    except AttributeError:
         print("  ERROR: Could not find function to start select handler in operators.py")
    except Exception as e:
        print(f"  ERROR: Failed to start modal select handler: {e}")

    # Trigger Initial Update Checks (using function in update_manager)
    print("FieldForge: Scheduling initial update checks...")
    try:
        # Use a timer for initial checks
        bpy.app.timers.register(update_manager.initial_update_check_all, first_interval=1.0)
    except Exception as e:
        print(f"  ERROR: Failed to schedule initial update check: {e}")


    # print(f"FieldForge: Registration complete.")
    # Force redraw after registration
    drawing.tag_redraw_all_view3d()


# --- Unregistration ---
def unregister():
    # print(f"\nUnregistering {bl_info['name']} addon...")

    # Unregister Classes (in reverse order)
    print("FieldForge: Unregistering classes...")
    for cls in reversed(classes_to_register):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
             pass # Ignore if already unregistered or Blender shutting down
        except Exception as e:
            print(f"  ERROR: Failed to unregister class {cls.__name__}: {e}")

    # Remove Handlers
    print("FieldForge: Removing handlers...")
    try:
        # Depsgraph
        if update_manager.ff_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(update_manager.ff_depsgraph_handler)

        # Draw Handler (using handle stored in drawing module)
        if drawing._draw_handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(drawing._draw_handle, 'WINDOW')
                print("  - Custom Draw Handler unregistered.")
            except ValueError: pass # Already removed
            except Exception as e_draw: print(f"  WARN: Error removing draw handler: {e_draw}")
            drawing._draw_handle = None # Clear handle in module

        # Stop Modal Operator (Best effort: rely on its cancel/timer checks)
        # Setting the global flag helps it stop cleanly on next tick/timer
        if operators._selection_handler_running: # Assuming flag defined in operators.py
             print("  - Signaling modal select handler to stop...")
             operators._selection_handler_running = False
             # We don't have the instance here to call cancel_modal directly

    except Exception as e:
        print(f"  ERROR: Error removing handlers: {e}")

    # Remove Menu Items
    print("FieldForge: Removing menus...")
    try:
        bpy.types.VIEW3D_MT_add.remove(menus.menu_func)
    except ValueError: pass # Already removed
    except Exception as e: print(f"  WARN: Error removing menu item: {e}")


    # Clear Timers and State (call functions in relevant modules)
    print("FieldForge: Clearing timers and state...")
    try:
        update_manager.clear_timers_and_state() # Assumes function exists in update_manager
        drawing.clear_draw_data() # Assumes function exists in drawing
        # operators module might reset its flag automatically or on next invoke
    except Exception as e:
        print(f"  WARN: Error clearing addon state: {e}")
        
    drawing.clear_draw_data()

    # Unregister Properties (if any were registered)
    # try:
    #     del bpy.types.Object.my_prop_group
    # except (AttributeError, TypeError): pass
    # bpy.utils.unregister_class(MyPropertyGroup)

    # Clean up namespace a bit (optional)
    # del sys.modules[__name__ + ".core.update_manager"] # Example - be careful with this

    print("FieldForge: Unregistration complete.")
    # Force redraw after unregistration
    try:
        drawing.tag_redraw_all_view3d()
    except Exception: pass # May fail if drawing module already unloaded