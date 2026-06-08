# FieldForge
# Copyright (C) 2026 Dejan Petrović <aonozan@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
bl_info = {
    "name": "FieldForge",
    "author": "Dejan Petrović (AonoZan)",
    "version": (0, 7, 2),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar (N-Panel) > FieldForge Tab | Add > Mesh > Field Forge SDF",
    "description": "Adds and manages dynamic SDF shapes using libfive with hierarchical blending, extrusion, and custom visuals",
    "warning": "Requires compiled libfive libraries.",
    "doc_url": "",
    "category": "Mesh",
    "license": "GPL-3.0-or-later",
}

import bpy
import os
import sys

addon_dir = os.path.dirname(os.path.realpath(__file__))

if addon_dir not in sys.path:
    sys.path.append(addon_dir)

libfive_base_dir = os.path.join(addon_dir, 'libfive', 'src')
libfive_folder_missing = not os.path.isdir(libfive_base_dir)
os.environ['LIBFIVE_FRAMEWORK_DIR'] = libfive_base_dir

libfive_available = False

if not libfive_folder_missing:
    if addon_dir not in sys.path:
        sys.path.append(addon_dir)

    os.environ['LIBFIVE_FRAMEWORK_DIR'] = libfive_base_dir

    try:
        import libfive
        import libfive.ffi as ffi
        import libfive.stdlib as lf
        import libfive.shape as libfive_shape_module

        core_so = ffi.lib
        stdlib_so = ffi.stdlib
        
        if hasattr(lf, 'sphere'):
            libfive_available = True
        else:
            raise ImportError("Core function check failed.")

        # --- Module Imports (Relative) ---
        # Use a structure that allows reloading during development if needed
        if "bpy" in locals():
            # Find all cached submodules starting with this addon's package name
            prefix = __name__ + "."
            for module_name in list(sys.modules.keys()):
                if module_name.startswith(prefix):
                    del sys.modules[module_name]
            
        from . import utils
        from .core import handlers
        from .core import state
        from .core import update_manager
        from . import drawing
        from .ui import operators
        from .ui import panels
        from .ui import menus

        # --- Collect Classes ---
        # Assumes each module defines a tuple/list named 'classes_to_register'
        classes_to_register = (
            *operators.classes_to_register,
            *panels.classes_to_register,
            *menus.classes_to_register,
        )

    except Exception as e:
        libfive_error = str(e)
        libfive_available = False

def assert_libfive_available():
    if libfive_folder_missing:
        raise RuntimeError(
            f"\n\n[FieldForge Error] Addon failed to load because the compiled libfive binaries are missing.\n"
            f"If you cloned this from the repository, please make sure you have initialized the submodules "
            f"and built/copied the compiled libraries to the correct path.\n"
            f"Expected folder: {libfive_base_dir}\n"
        )

    # Case B: The folder exists, but the library failed to load (e.g. wrong OS architecture, missing dependencies)
    if not libfive_available:
        raise RuntimeError(
            f"\n\n[FieldForge Error] Addon failed to load because libfive is missing or corrupt.\n"
            f"Details: {libfive_error}\n"
        )

# --- Registration ---
def register():
    assert_libfive_available()

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
    
    bpy.types.Object.sdf_color = bpy.props.FloatVectorProperty(
        name="Color",
        subtype='COLOR',
        size=4,
        default=(0.8, 0.8, 0.8, 1.0),
        min=0.0, max=1.0,
        description="RGBA color associated with this shape"
    )

    # Force redraw after registration
    drawing.tag_redraw_all_view3d()


# --- Unregistration ---
def unregister():
    assert_libfive_available()

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