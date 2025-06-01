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
    OBJECT_OT_add_sdf_group,
    OBJECT_OT_add_sdf_canvas,
    OBJECT_OT_add_sdf_cube_source,
    OBJECT_OT_add_sdf_sphere_source,
    OBJECT_OT_add_sdf_cylinder_source,
    OBJECT_OT_add_sdf_cone_source,
    OBJECT_OT_add_sdf_pyramid_source,
    OBJECT_OT_add_sdf_torus_source,
    OBJECT_OT_add_sdf_rounded_box_source,
    OBJECT_OT_add_sdf_circle_source,
    OBJECT_OT_add_sdf_ring_source,
    OBJECT_OT_add_sdf_polygon_source,
    OBJECT_OT_add_sdf_text_source,
    OBJECT_OT_add_sdf_half_space_source,
)


# --- Add Menu Definition ---

class VIEW3D_MT_add_sdf(Menu):
    """Add menu for FieldForge SDF objects (Bounds, Groups, and Sources)"""
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
        parent_is_valid_for_any_child = active_obj is not None and \
                         (active_obj.get(constants.SDF_BOUNDS_MARKER, False) or
                          active_obj.get(constants.SDF_GROUP_MARKER, False) or
                          active_obj.get(constants.SDF_CANVAS_MARKER, False) or
                          utils.is_sdf_source(active_obj) or
                          utils.find_parent_bounds(active_obj) is not None)

        # Use a column layout for the source shapes section
        col = layout.column()
        col.enabled = parent_is_valid_for_any_child

        col.label(text="Add Control Object (Child of Active):")
        op_group = col.operator(OBJECT_OT_add_sdf_group.bl_idname, text="Group", icon='GROUP')
        op_canvas_layout = col.row()
        op_canvas_layout.enabled = not active_obj.get(constants.SDF_CANVAS_MARKER, False) if active_obj else True
        op_canvas = op_canvas_layout.operator(OBJECT_OT_add_sdf_canvas.bl_idname, text="2D Canvas", icon='OUTLINER_OB_SURFACE')
        col.separator()

        # --- Add Source Shapes ---
        col.label(text="Add Source Shape (Child of Active):")
        # List all available source shape operators using their bl_idnames
        is_canvas_active = active_obj.get(constants.SDF_CANVAS_MARKER, False) if active_obj else False
        col_3d = col.column()
        col_3d.enabled = not is_canvas_active
        col_3d.operator(OBJECT_OT_add_sdf_cube_source.bl_idname, text="Cube", icon='MESH_CUBE')
        col_3d.operator(OBJECT_OT_add_sdf_sphere_source.bl_idname, text="Sphere", icon='MESH_UVSPHERE')
        col_3d.operator(OBJECT_OT_add_sdf_cylinder_source.bl_idname, text="Cylinder", icon='MESH_CYLINDER')
        col_3d.operator(OBJECT_OT_add_sdf_cone_source.bl_idname, text="Cone", icon='MESH_CONE')
        col_3d.operator(OBJECT_OT_add_sdf_pyramid_source.bl_idname, text="Pyramid", icon='MESH_CONE')
        col_3d.operator(OBJECT_OT_add_sdf_torus_source.bl_idname, text="Torus", icon='MESH_TORUS')
        col_3d.operator(OBJECT_OT_add_sdf_rounded_box_source.bl_idname, text="Rounded Box", icon='MOD_BEVEL')
        col_3d.operator(OBJECT_OT_add_sdf_circle_source.bl_idname, text="Circle", icon='MESH_CIRCLE')
        col_3d.operator(OBJECT_OT_add_sdf_ring_source.bl_idname, text="Ring", icon='CURVE_NCIRCLE')
        col_3d.operator(OBJECT_OT_add_sdf_polygon_source.bl_idname, text="Polygon", icon='MESH_CIRCLE')
        col_3d.operator(OBJECT_OT_add_sdf_text_source.bl_idname, text="Text", icon='OUTLINER_OB_FONT')
        col_3d.operator(OBJECT_OT_add_sdf_half_space_source.bl_idname, text="Half Space", icon='MESH_PLANE')

        col_2d = col.column()
        col_2d.operator(OBJECT_OT_add_sdf_circle_source.bl_idname, text="Circle", icon='MESH_CIRCLE')
        col_2d.operator(OBJECT_OT_add_sdf_ring_source.bl_idname, text="Ring", icon='CURVE_NCIRCLE')
        col_2d.operator(OBJECT_OT_add_sdf_polygon_source.bl_idname, text="Polygon", icon='MESH_CIRCLE')
        col_2d.operator(OBJECT_OT_add_sdf_text_source.bl_idname, text="Text", icon='OUTLINER_OB_FONT')

        # Optional: Add informational text below if adding sources is disabled
        if not parent_is_valid_for_any_child:
             layout.separator()
             col_info = layout.column()
             col_info.active = False # Make text greyed out
             col_info.label(text="Select Bounds, Group, Canvas or Source object", icon='INFO')
             col_info.label(text="to add new child items.")


# --- Menu Function (for Appending) ---

def menu_func(self, context):
    """ Function called by Blender to draw the menu item in the main Add > Mesh menu. """
    # Adds the Field Forge submenu defined above
    self.layout.menu(VIEW3D_MT_add_sdf.bl_idname, icon='MOD_OPACITY')


# --- List of Menu Classes to Register ---
classes_to_register = (
    VIEW3D_MT_add_sdf,
)