# FieldForge/ui/menus.py

"""
Defines Blender Menu classes and functions for the FieldForge addon,
primarily for the Add menu (Shift+A).
"""

import bpy
from bpy.types import Menu

# Use relative imports assuming this file is in FieldForge/ui/
from .. import constants
from .. import utils # For find_parent_bounds, is_sdf_source
# Import operator IDs needed for menu items
from .operators import (
    OBJECT_OT_add_sdf_bounds,
    OBJECT_OT_add_sdf_cube_source,
    OBJECT_OT_add_sdf_sphere_source,
    OBJECT_OT_add_sdf_cylinder_source,
    OBJECT_OT_add_sdf_cone_source,
    OBJECT_OT_add_sdf_torus_source,
    OBJECT_OT_add_sdf_rounded_box_source,
    OBJECT_OT_add_sdf_circle_source,
    OBJECT_OT_add_sdf_ring_source,
    OBJECT_OT_add_sdf_polygon_source,
    OBJECT_OT_add_sdf_half_space_source,
    # Add other operator bl_idname imports if needed for menu items
)


# --- Add Menu Definition ---

class VIEW3D_MT_add_sdf(Menu):
    """Add menu for FieldForge SDF objects (Bounds controller and Source shapes)"""
    bl_idname = "VIEW3D_MT_add_sdf" # Unique ID for this menu
    bl_label = "Field Forge SDF"    # Label displayed for the submenu

    def draw(self, context):
        layout = self.layout

        # Check if libfive is available (using lf object set in root __init__)
        if utils.lf is None:
            layout.label(text="libfive library not found!", icon='ERROR')
            layout.label(text="Please check console for details.")
            return

        # --- Add Bounds Controller ---
        # This is always available (doesn't depend on selection)
        layout.operator(OBJECT_OT_add_sdf_bounds.bl_idname, text="Add Bounds Controller", icon='MOD_BUILD')
        layout.separator()

        # --- Add Source Shapes ---
        # These should only be enabled if the active object can be a parent
        active_obj = context.active_object
        can_add_source = active_obj is not None and \
                         (active_obj.get(constants.SDF_BOUNDS_MARKER, False) or
                          utils.find_parent_bounds(active_obj) is not None)

        # Use a column layout for the source shapes section
        col = layout.column()
        # Enable/disable the whole source shapes section based on context
        col.enabled = can_add_source

        col.label(text="Add Source Shape (Child of Active):")
        # List all available source shape operators using their bl_idnames
        col.operator(OBJECT_OT_add_sdf_cube_source.bl_idname, text="Cube", icon='MESH_CUBE')
        col.operator(OBJECT_OT_add_sdf_sphere_source.bl_idname, text="Sphere", icon='MESH_UVSPHERE')
        col.operator(OBJECT_OT_add_sdf_cylinder_source.bl_idname, text="Cylinder", icon='MESH_CYLINDER')
        col.operator(OBJECT_OT_add_sdf_cone_source.bl_idname, text="Cone", icon='MESH_CONE')
        col.operator(OBJECT_OT_add_sdf_torus_source.bl_idname, text="Torus", icon='MESH_TORUS')
        col.operator(OBJECT_OT_add_sdf_rounded_box_source.bl_idname, text="Rounded Box", icon='MOD_BEVEL')
        col.operator(OBJECT_OT_add_sdf_circle_source.bl_idname, text="Circle", icon='MESH_CIRCLE')
        col.operator(OBJECT_OT_add_sdf_ring_source.bl_idname, text="Ring", icon='CURVE_NCIRCLE') # Or MESH_TORUS? CURVE_NCIRCLE is good
        col.operator(OBJECT_OT_add_sdf_polygon_source.bl_idname, text="Polygon", icon='MESH_CIRCLE') # Shares icon with Circle
        col.operator(OBJECT_OT_add_sdf_half_space_source.bl_idname, text="Half Space", icon='MESH_PLANE')

        # Optional: Add informational text below if adding sources is disabled
        if not can_add_source:
             layout.separator()
             col_info = layout.column()
             col_info.active = False # Make text greyed out
             col_info.label(text="Select Bounds or Source object", icon='INFO')
             col_info.label(text="to add new child shapes.")


# --- Menu Function (for Appending) ---

def menu_func(self, context):
    """ Function called by Blender to draw the menu item in the main Add > Mesh menu. """
    # Adds the Field Forge submenu defined above
    self.layout.menu(VIEW3D_MT_add_sdf.bl_idname, icon='MOD_OPACITY') # Example icon


# --- List of Menu Classes to Register ---
classes_to_register = (
    VIEW3D_MT_add_sdf,
)