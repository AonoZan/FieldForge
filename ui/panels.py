"""
Defines Blender Panel classes for the FieldForge addon UI,
displayed in the 3D Viewport Sidebar (N-Panel).
Includes helper functions for drawing UI elements.
"""

import bpy
from bpy.types import Panel

# Use relative imports assuming this file is in FieldForge/ui/
from .. import constants
from .. import utils # For find_parent_bounds, is_sdf_source, is_valid_2d_loft_source, get_bounds_setting
# Import operator IDs needed for buttons (operators are defined in operators.py)
from .operators import (
    OBJECT_OT_sdf_manual_update,
    OBJECT_OT_fieldforge_toggle_array_axis,
    OBJECT_OT_fieldforge_set_main_array_mode,
    OBJECT_OT_fieldforge_set_csg_mode,
    OBJECT_OT_fieldforge_reorder_source,
)

# --- UI Drawing Helper Functions ---

def draw_sdf_bounds_settings(layout: bpy.types.UILayout, context: bpy.types.Context):
    """ Draws the UI elements for the Bounds object settings. """
    obj = context.object # Assumes the active object IS the Bounds object

    col = layout.column(align=True)

    # Resolution Box
    box_res = col.box()
    box_res.label(text="Resolution:")
    row_res = box_res.row(align=True)
    # Access custom properties using [] notation
    row_res.prop(obj, '["sdf_viewport_resolution"]', text="Viewport")
    row_res.prop(obj, '["sdf_final_resolution"]', text="Final")

    # Timing Box
    box_time = col.box()
    box_time.label(text="Update Timing:")
    row_time1 = box_time.row(align=True)
    row_time1.prop(obj, '["sdf_realtime_update_delay"]', text="Inactive Delay")
    row_time2 = box_time.row(align=True)
    row_time2.prop(obj, '["sdf_minimum_update_interval"]', text="Min Interval")

    # Blending Box (Global for children of this bounds)
    box_blend = col.box()
    box_blend.label(text="Root Child Blending:")
    row_blend = box_blend.row(align=True)
    row_blend.prop(obj, '["sdf_global_blend_factor"]', text="Blend Factor")

    col.separator()

    # Update Controls Box
    box_upd = col.box()
    box_upd.label(text="Update & Display:")
    row_upd1 = box_upd.row(align=True)
    row_upd1.prop(obj, '["sdf_auto_update"]', text="Auto Viewport Update", toggle=True)
    # Manual Update Button - Use imported bl_idname
    row_upd1.operator(OBJECT_OT_sdf_manual_update.bl_idname, text="Update Final", icon='FILE_REFRESH')

    row_upd2 = box_upd.row(align=True)
    row_upd2.prop(obj, '["sdf_show_source_empties"]', text="Show Source Visuals") # Controls custom draw + empty visibility
    row_upd3 = box_upd.row(align=True)
    row_upd3.prop(obj, '["sdf_create_result_object"]', text="Create Result If Missing")
    row_upd4 = box_upd.row(align=True)
    row_upd4.prop(obj, '["sdf_discard_mesh_on_save"]', text="Discard Mesh on Save")

    col.separator()

    # Result Object Box
    box_res_obj = col.box()
    box_res_obj.label(text="Result Object:")
    # Get current name safely using constant key
    result_name = obj.get(constants.SDF_RESULT_OBJ_NAME_PROP, "")
    row_res_obj1 = box_res_obj.row(align=True)
    # Access property for display/editing
    row_res_obj1.prop(obj, f'["{constants.SDF_RESULT_OBJ_NAME_PROP}"]', text="Name")
    # Decide if name should be editable. Making it read-only simplifies things.
    # row_res_obj1.enabled = False # Keep read-only for now

    row_res_obj2 = box_res_obj.row(align=True)
    # Button to select the result object
    op = row_res_obj2.operator("object.select_pattern", text="Select Result Object", icon='VIEWZOOM')
    op.pattern = result_name
    op.extend = False
    # Disable button if name empty or object not found
    row_res_obj2.enabled = bool(result_name and result_name in context.scene.objects)


def draw_sdf_source_info(layout: bpy.types.UILayout, context: bpy.types.Context):
    """ Draws the UI elements for the SDF Source object properties. """
    obj = context.object # Assumes active object is an SDF Source
    sdf_type = obj.get("sdf_type", "Unknown")

    col = layout.column()

    # Hierarchy (Up/Down) buttons
    if obj.parent:
        box_hierarchy = col.box()
        hier_col = box_hierarchy.column(align=True)
        hier_col.label(text="Processing Order:")

        can_move_up = False
        can_move_down = False

        sdf_siblings = []
        for child in obj.parent.children:
            if utils.is_sdf_source(child) and child.visible_get(view_layer=context.view_layer):
                sdf_siblings.append(child)

        if len(sdf_siblings) > 1:
            def get_sort_key(c):
                return (c.get("sdf_processing_order", float('inf')), c.name)
            sdf_siblings.sort(key=get_sort_key)

            try:
                idx = sdf_siblings.index(obj)
                if idx > 0:
                    can_move_up = True
                if idx < len(sdf_siblings) - 1:
                    can_move_down = True
            except ValueError:
                pass

        row_order = hier_col.row(align=True)

        up_button_layout = row_order.row()
        up_button_layout.active = can_move_up
        op_up = up_button_layout.operator(OBJECT_OT_fieldforge_reorder_source.bl_idname, text="", icon='TRIA_UP')
        op_up.direction = 'UP'

        down_button_layout = row_order.row() 
        down_button_layout.active = can_move_down
        op_down = down_button_layout.operator(OBJECT_OT_fieldforge_reorder_source.bl_idname, text="", icon='TRIA_DOWN')
        op_down.direction = 'DOWN'
        
        current_order_num = obj.get("sdf_processing_order", "N/A")
        row_order.label(text=f"  Order: {current_order_num}")

    col.separator()
    # Basic Info
    col.label(text=f"SDF Type: {sdf_type.capitalize()}")
    col.separator()

    # --- Interaction Mode ---
    box_interact = col.box()
    interact_col = box_interact.column(align=True)
    interact_col.label(text="Interaction Mode:")

    # Get current states
    use_loft = obj.get("sdf_use_loft", False)
    use_morph = obj.get("sdf_use_morph", False) and not use_loft # Exclusive
    use_clearance = obj.get("sdf_use_clearance", False) and not use_loft and not use_morph # Exclusive
    csg_active = not use_loft and not use_morph and not use_clearance

    row_csg = interact_col.row(align=True)
    row_csg.active = csg_active # Disable if morph/clearance/loft is active
    current_csg_op = obj.get("sdf_csg_operation", constants.DEFAULT_SOURCE_SETTINGS["sdf_csg_operation"])

    op_none = row_csg.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="", icon='RADIOBUT_OFF', depress=(current_csg_op == 'NONE'))
    op_none.csg_mode = 'NONE'
    op_union = row_csg.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="", icon='ADD', depress=(current_csg_op == 'UNION'))
    op_union.csg_mode = 'UNION'
    op_intersect = row_csg.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="", icon='SELECT_INTERSECT', depress=(current_csg_op == 'INTERSECT'))
    op_intersect.csg_mode = 'INTERSECT'
    op_diff = row_csg.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="", icon='SELECT_DIFFERENCE', depress=(current_csg_op == 'DIFFERENCE'))
    op_diff.csg_mode = 'DIFFERENCE'

    if not csg_active:
        interact_col.label(text="(CSG mode overridden by Loft/Morph/Clearance)")

    # Loft Toggle
    # Loft validity can be complex, for UI simplicity, just show the toggle. Logic is in sdf_logic.
    if sdf_type in ["circle", "polygon", "ring"]:
        row_loft = interact_col.row(align=True)
        row_loft.prop(obj, '["sdf_use_loft"]', text="Use Loft", toggle=True, icon='IPO_LINEAR')

    # Morph Toggle & Factor
    row_morph = interact_col.row(align=True); row_morph.active = not use_loft
    row_morph.prop(obj, '["sdf_use_morph"]', text="Morph", toggle=True, icon='MOD_SIMPLEDEFORM')
    sub_morph = row_morph.row(align=True); sub_morph.active = use_morph and not use_loft
    sub_morph.prop(obj, '["sdf_morph_factor"]', text="Factor")

    # Clearance Toggle & Offset
    row_clearance = interact_col.row(align=True); row_clearance.active = not use_loft and not use_morph
    row_clearance.prop(obj, '["sdf_use_clearance"]', text="Clearance", toggle=True, icon='MOD_OFFSET')
    sub_clearance = row_clearance.row(align=True); sub_clearance.active = use_clearance and not use_loft and not use_morph
    sub_clearance.prop(obj, '["sdf_clearance_offset"]', text="Offset")
    if use_clearance and not use_loft and not use_morph:
        row_clearance_keep = interact_col.row(align=True); row_clearance_keep.active = True
        row_clearance_keep.prop(obj, '["sdf_clearance_keep_original"]', text="Keep Original Shape")

    col.separator()

    # --- Shape Parameters ---
    param_box = col.box()
    param_col = param_box.column(align=True)
    has_params = False # Track if any params are shown for this type

    if sdf_type == "text":
        param_col.label(text="Parameters:")
        param_col.prop(obj, '["sdf_text_string"]', text="Text")
        has_params = True
    elif sdf_type == "rounded_box":
        param_col.label(text="Parameters:")
        param_col.prop(obj, '["sdf_round_radius"]', text="Rounding Radius")
        has_params = True
    elif sdf_type == "torus":
        param_col.label(text="Parameters (Unit Space):")
        param_col.prop(obj, '["sdf_torus_major_radius"]', text="Major Radius")
        param_col.prop(obj, '["sdf_torus_minor_radius"]', text="Minor Radius")
        maj_r = obj.get("sdf_torus_major_radius", 0.35); min_r = obj.get("sdf_torus_minor_radius", 0.15)
        if min_r >= maj_r: param_col.label(text="Minor radius should be < Major", icon='ERROR')
        has_params = True
    elif sdf_type in {"circle", "ring", "polygon"}:
        param_col.label(text="Parameters:")
        param_col.prop(obj, '["sdf_extrusion_depth"]', text="Extrusion Depth")
        if sdf_type == "ring":
            param_col.prop(obj, '["sdf_inner_radius"]', text="Inner Radius (Unit)")
        elif sdf_type == "polygon":
            param_col.prop(obj, '["sdf_sides"]', text="Sides")
        has_params = True
    elif sdf_type == "half_space":
        param_col.label(text="Defined by Transform:")
        param_col.label(text="- Origin: Point on plane")
        param_col.label(text="- Local +Z: Outward Normal")
        has_params = True

    # Fallback text if no specific params shown
    if not has_params:
        param_col.label(text="Parameters: None (Defined by Transform)")

    param_box.active = has_params # Grey out box if no relevant params

    col.separator()

    # --- Shell Modifier ---
    box_shell = col.box()
    shell_col = box_shell.column(align=True)
    shell_col.label(text="Shell Modifier:")
    use_shell = obj.get("sdf_use_shell", False)
    shell_col.prop(obj, '["sdf_use_shell"]', text="Use Shell", toggle=True, icon='MOD_SOLIDIFY')
    row_shell_offset = shell_col.row(); row_shell_offset.active = use_shell
    row_shell_offset.prop(obj, '["sdf_shell_offset"]', text="Thickness")

    col.separator()

    # --- Child Object Blending ---
    box_child_blend = col.box()
    blend_col = box_child_blend.column(align=True)
    blend_col.label(text="Child Object Blending:")
    # Blend factor applies to children ADDED to this one. Disable if clearance is on?
    box_child_blend.active = not use_clearance # Can't blend into a clearance op
    blend_col.prop(obj, '["sdf_child_blend_factor"]', text="Factor")
    blend_col.label(text="(Smoothness for children parented TO this one)")
    if use_clearance: blend_col.label(text="(Disabled when 'Use Clearance' is active)")

    col.separator()

    # --- Array Modifier ---
    box_array = col.box()
    main_arr_col = box_array.column()
    main_arr_col.label(text="Array Modifier:")

    # Mode Buttons
    main_mode_prop = "sdf_main_array_mode"; current_main_mode = obj.get(main_mode_prop, 'NONE')
    row_mode = main_arr_col.row(align=True)
    op_none = row_mode.operator(OBJECT_OT_fieldforge_set_main_array_mode.bl_idname, text="None", depress=(current_main_mode == 'NONE')); op_none.main_mode = 'NONE'
    op_lin = row_mode.operator(OBJECT_OT_fieldforge_set_main_array_mode.bl_idname, text="Linear", depress=(current_main_mode == 'LINEAR')); op_lin.main_mode = 'LINEAR'
    op_rad = row_mode.operator(OBJECT_OT_fieldforge_set_main_array_mode.bl_idname, text="Radial", depress=(current_main_mode == 'RADIAL')); op_rad.main_mode = 'RADIAL'
    main_arr_col.separator()

    # Conditional UI
    if current_main_mode == 'LINEAR':
        linear_col = main_arr_col.column(align=False)
        act_x="sdf_array_active_x"; del_x="sdf_array_delta_x"; cnt_x="sdf_array_count_x"
        act_y="sdf_array_active_y"; del_y="sdf_array_delta_y"; cnt_y="sdf_array_count_y"
        act_z="sdf_array_active_z"; del_z="sdf_array_delta_z"; cnt_z="sdf_array_count_z"
        is_x = obj.get(act_x, False); is_y = obj.get(act_y, False); is_z = obj.get(act_z, False)
        # X Axis
        row_x = linear_col.row(align=True); op_x=row_x.operator(OBJECT_OT_fieldforge_toggle_array_axis.bl_idname, text="X", depress=is_x); op_x.axis='X'
        sub_x = row_x.row(align=True); sub_x.active = is_x; sub_x.prop(obj, f'["{del_x}"]', text="Delta"); sub_x.prop(obj, f'["{cnt_x}"]', text="Count")
        # Y Axis (only if X active)
        row_y = linear_col.row(align=True); row_y.active = is_x; op_y=row_y.operator(OBJECT_OT_fieldforge_toggle_array_axis.bl_idname, text="Y", depress=is_y); op_y.axis='Y'
        sub_y = row_y.row(align=True); sub_y.active = is_y; sub_y.prop(obj, f'["{del_y}"]', text="Delta"); sub_y.prop(obj, f'["{cnt_y}"]', text="Count")
        # Z Axis (only if Y active)
        row_z = linear_col.row(align=True); row_z.active = is_y; op_z=row_z.operator(OBJECT_OT_fieldforge_toggle_array_axis.bl_idname, text="Z", depress=is_z); op_z.axis='Z'
        sub_z = row_z.row(align=True); sub_z.active = is_z; sub_z.prop(obj, f'["{del_z}"]', text="Delta"); sub_z.prop(obj, f'["{cnt_z}"]', text="Count")

    elif current_main_mode == 'RADIAL':
        radial_col = main_arr_col.column(align=True)
        cnt_r = "sdf_radial_count"; cen_r = "sdf_radial_center"
        radial_col.prop(obj, f'["{cnt_r}"]', text="Count")
        radial_col.prop(obj, f'["{cen_r}"]', text="Center Offset")
    if current_main_mode != 'NONE':
        prop_center_on_origin = "sdf_array_center_on_origin"
        row_center_origin = main_arr_col.row()
        row_center_origin.prop(obj, f'["{prop_center_on_origin}"]', text="Center on Origin")

    col.separator()
    # Info Text
    col.label(text="Transform controls placement.")


# --- Main Panel Class ---

class VIEW3D_PT_fieldforge_main(Panel):
    """Main FieldForge Panel in the 3D Viewport Sidebar (N-Panel)"""
    bl_label = "FieldForge Controls"
    bl_idname = "VIEW3D_PT_fieldforge_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FieldForge" # Tab name

    # Optional: @classmethod poll(cls, context) to disable panel if libfive not available?
    @classmethod
    def poll(cls, context):
        return utils.lf is not None

    def draw(self, context):
        layout = self.layout
        obj = context.object

        # Check again in draw, just in case poll changes
        if utils.lf is None:
            layout.label(text="libfive library not found!", icon='ERROR')
            layout.separator()
            layout.label(text="Check Blender Console.")
            layout.label(text="Dynamic features disabled.")
            return

        if not obj:
            layout.label(text="Select a FieldForge object.", icon='INFO')
            return

        # Check if active object is a Bounds controller
        if obj.get(constants.SDF_BOUNDS_MARKER, False):
            layout.label(text=f"Bounds: {obj.name}", icon='MOD_BUILD')
            layout.separator()
            draw_sdf_bounds_settings(layout, context)

        # Check if active object is an SDF Source
        elif utils.is_sdf_source(obj):
            layout.label(text=f"Source: {obj.name}", icon='OBJECT_DATA')
            layout.separator()
            draw_sdf_source_info(layout, context)

        else:
            # Active object is not part of FieldForge system
            layout.label(text="Not a FieldForge object.", icon='QUESTION')


# --- List of Panel Classes to Register ---
classes_to_register = (
    VIEW3D_PT_fieldforge_main,
)