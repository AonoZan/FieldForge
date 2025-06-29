# FieldForge/__init__.py

bl_info = {
    "name": "FieldForge",
    "author": "AonoZan & libfive Team",
    "version": (0, 6, 0),
    "blender": (4, 1, 0),
    "location": "View3D > Sidebar (N-Panel) > FieldForge Tab | Add > Mesh > Field Forge SDF",
    "description": "Adds and manages dynamic SDF shapes using libfive with hierarchical blending, extrusion, and custom visuals",
    "warning": "Requires compiled libfive libraries.",
    "doc_url": "",
    "category": "Add Mesh",
    "license": "GPL-3.0-only",
}

import bpy
import os
import sys

# --- Libfive Loading and Setup ---
addon_dir = os.path.dirname(os.path.realpath(__file__))
# Assuming libfive python wrapper is directly in addon_dir or a subdir detectable from there
libfive_python_dir = addon_dir
if libfive_python_dir not in sys.path:
    sys.path.append(libfive_python_dir)

# Path to the actual compiled libraries (assuming a specific structure)
# Adjust this path if your bundled library structure is different
libfive_base_dir = os.path.join(addon_dir, 'libfive', 'src')

# Set environment variable *before* ffi import (if needed by your ffi.py)
# This tells ffi.py where to look for the compiled libs (.so, .dll, .dylib)
if os.path.isdir(libfive_base_dir):
    os.environ['LIBFIVE_FRAMEWORK_DIR'] = libfive_base_dir
else:
    print(f"FieldForge: Warning - Libfive library directory not found at: {libfive_base_dir}")

libfive_available = False
lf = None
ffi = None
try:
    # Import libfive components (assuming they are importable after path setup)
    import libfive.ffi as ffi
    import libfive.shape # Import shape early if stdlib depends on it
    import libfive.stdlib as lf

    # Basic check for successful loading
    if hasattr(lf, 'sphere') and hasattr(ffi.lib, 'libfive_tree_const'):
        libfive_available = True
    else:
         print("FieldForge: Libfive imported but core function check failed.")
         raise ImportError("Core function check failed")

except ImportError as e:
    # Provide guidance based on expected structure
    core_lib_path = os.path.join(libfive_base_dir, "src", f"libfive.{'dll' if sys.platform == 'win32' else 'dylib' if sys.platform == 'darwin' else 'so'}")
    stdlib_lib_path = os.path.join(libfive_base_dir, "stdlib", f"libfive-stdlib.{'dll' if sys.platform == 'win32' else 'dylib' if sys.platform == 'darwin' else 'so'}")
    current_env_var = os.environ.get('LIBFIVE_FRAMEWORK_DIR', '<Not Set>')
    print("FieldForge: Addon requires libfive. Dynamic functionality disabled.")
except Exception as e:
    traceback.print_exc()
    print("FieldForge: Dynamic functionality disabled.")

# --- Module Imports (Relative) ---
# Use a structure that allows reloading during development if needed
if "bpy" in locals():
    import importlib
    # Order can matter if modules depend on each other
    from . import constants
    from . import utils
    from .core import handlers
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
    from .core import handlers
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

# --- Collect Classes ---
# Assumes each module defines a tuple/list named 'classes_to_register'
classes_to_register = (
    *operators.classes_to_register,
    *panels.classes_to_register,
    *menus.classes_to_register,
)

# --- Registration ---
def register():
    if not libfive_available:
        print("FieldForge: Libfive not available, registration incomplete.")
        return

    # Register Classes
    for cls in classes_to_register:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
        except Exception as e:
            print(f"  ERROR: Failed to register class {cls.__name__}: {e}")

    # Add Menu Items
    try:
        bpy.types.VIEW3D_MT_add.append(menus.menu_func)
    except Exception as e:
        print(f"  ERROR: Could not add menu item: {e}")

    # Register Handlers
    try:
        # Depsgraph Handler
        if update_manager.ff_depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(update_manager.ff_depsgraph_handler)

        # Save Handlers
        if handlers.ff_save_pre_handler not in bpy.app.handlers.save_pre:
            bpy.app.handlers.save_pre.append(handlers.ff_save_pre_handler)
        if handlers.ff_save_post_handler not in bpy.app.handlers.save_post:
            bpy.app.handlers.save_post.append(handlers.ff_save_post_handler)

        if handlers.ff_load_post_handler not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(handlers.ff_load_post_handler)

        # Undo Handlers
        if handlers.ff_undo_pre_handler not in bpy.app.handlers.undo_pre:
            bpy.app.handlers.undo_pre.append(handlers.ff_undo_pre_handler)
        if handlers.ff_undo_post_handler not in bpy.app.handlers.undo_post:
            bpy.app.handlers.undo_post.append(handlers.ff_undo_post_handler)

        # Draw Handler (store handle within drawing module)
        if drawing._draw_handle is None:
            drawing._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
                drawing.ff_draw_callback, (), 'WINDOW', 'POST_VIEW'
            )
    except Exception as e:
        print(f"  ERROR: Failed registering handlers: {e}")

    # Trigger Initial Update Checks (using function in update_manager)
    try:
        # Use a timer for initial checks
        bpy.app.timers.register(update_manager.initial_update_check_all, first_interval=1.0)
    except Exception as e:
        print(f"  ERROR: Failed to schedule initial update check: {e}")

    # Force redraw after registration
    drawing.tag_redraw_all_view3d()


# --- Unregistration ---
def unregister():

    # Unregister Classes (in reverse order)
    for cls in reversed(classes_to_register):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
             pass # Ignore if already unregistered or Blender shutting down
        except Exception as e:
            print(f"  ERROR: Failed to unregister class {cls.__name__}: {e}")

    # Remove Handlers
    try:
        # Depsgraph
        if update_manager.ff_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(update_manager.ff_depsgraph_handler)

        # Save Handlers
        if handlers.ff_save_pre_handler in bpy.app.handlers.save_pre:
            bpy.app.handlers.save_pre.remove(handlers.ff_save_pre_handler)
        if handlers.ff_save_post_handler in bpy.app.handlers.save_post:
            bpy.app.handlers.save_post.remove(handlers.ff_save_post_handler)

        # Load Post Handler
        if handlers.ff_load_post_handler in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(handlers.ff_load_post_handler)

        # Undo Handlers
        if handlers.ff_undo_pre_handler in bpy.app.handlers.undo_pre:
            bpy.app.handlers.undo_pre.remove(handlers.ff_undo_pre_handler)
        if handlers.ff_undo_post_handler in bpy.app.handlers.undo_post:
            bpy.app.handlers.undo_post.remove(handlers.ff_undo_post_handler)

        # Draw Handler (using handle stored in drawing module)
        if drawing._draw_handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(drawing._draw_handle, 'WINDOW')
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
    try:
        bpy.types.VIEW3D_MT_add.remove(menus.menu_func)
    except ValueError: pass # Already removed
    except Exception as e: print(f"  WARN: Error removing menu item: {e}")


    # Clear Timers and State (call functions in relevant modules)
    try:
        update_manager.clear_timers_and_state() # Assumes function exists in update_manager
        drawing.clear_draw_data() # Assumes function exists in drawing
        # operators module might reset its flag automatically or on next invoke
    except Exception as e:
        print(f"  WARN: Error clearing addon state: {e}")

    # Force redraw after unregistration
    try:
        drawing.tag_redraw_all_view3d()
    except Exception: pass # May fail if drawing module already unloaded