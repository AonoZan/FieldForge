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
    OBJECT_OT_fieldforge_toggle_group_symmetry,
    OBJECT_OT_fieldforge_toggle_group_taper_z,
    OBJECT_OT_fieldforge_toggle_group_shear_x_by_y,
    OBJECT_OT_fieldforge_set_group_attract_repel_mode,
    OBJECT_OT_fieldforge_toggle_group_twirl,
    OBJECT_OT_fieldforge_set_group_twirl_axis,
    OBJECT_OT_add_sdf_canvas,
    OBJECT_OT_fieldforge_toggle_canvas_revolve,
)

# --- UI Drawing Helper Functions ---

def _draw_link_controls(layout: bpy.types.UILayout, context: bpy.types.Context, obj: bpy.types.Object, linkable_type_check_func):
    """
    Helper to draw link controls.
    """
    if not obj: return False, obj

    is_obj_actually_linked_to_valid_target = utils.is_sdf_linked(obj)

    row_link_target = layout.row(align=True)
    row_link_target.prop_search(obj, f'["{constants.SDF_LINK_TARGET_NAME_PROP}"]', 
                                context.scene, "objects", text="")
    effective_obj_for_ui = utils.get_effective_sdf_object(obj)
    toggle_op = row_link_target.operator(
        "object.fieldforge_toggle_process_linked_children", 
        text="", 
        icon='LINKED',
        depress=obj.get(constants.SDF_PROCESS_LINKED_CHILDREN_PROP, False)
    )
    
    layout.separator()
    return is_obj_actually_linked_to_valid_target, effective_obj_for_ui

def draw_sdf_bounds_settings(layout: bpy.types.UILayout, context: bpy.types.Context):
    """ Draws the UI elements for the Bounds object settings. """
    obj = context.object # Assumes the active object IS the Bounds object

    is_obj_itself_linked, obj_for_props = _draw_link_controls(layout, context, obj, utils.is_sdf_bounds)

    obj = obj_for_props

    # --- Resolution and Update Controls ---
    row_res_label = layout.row(align=True)
    row_res_label.label(text="Resolution:")
    row_res_props = layout.row(align=True)
    row_res_props.prop(obj_for_props, '["sdf_viewport_resolution"]', text="")
    row_res_props.prop(obj_for_props, '["sdf_auto_update"]', text="Auto Update", toggle=True)

    row_update_controls = layout.row(align=True)
    row_update_controls.prop(obj_for_props, '["sdf_final_resolution"]', text="")
    row_update_controls.operator(OBJECT_OT_sdf_manual_update.bl_idname, text="Manual Update", icon='FILE_REFRESH')

    layout.separator()

    # --- Material Assignment ---
    row_material_label = layout.row()
    row_material_label.label(text="Result Material:")

    layout.prop_search(obj_for_props, '["sdf_result_material_name"]', bpy.data, "materials", text="")

    layout.separator()

    # --- Update Timing ---
    row_timing_label = layout.row()
    row_timing_label.label(text="Update Timing:")
    
    row_timing_props = layout.row(align=True)
    row_timing_props.prop(obj_for_props, '["sdf_realtime_update_delay"]', text="Inactive Delay")
    row_timing_props.prop(obj_for_props, '["sdf_minimum_update_interval"]', text="Min Interval")

    layout.separator()

    # --- Display and Save Options ---
    row_display_options = layout.row()
    row_display_options.label(text="Options:")

    col_options = layout.column(align=True)
    col_options.prop(obj_for_props, '["sdf_show_source_empties"]', text="Show Visuals")
    col_options.prop(obj_for_props, '["sdf_create_result_object"]', text="Recreate Mesh if Missing")
    col_options.prop(obj_for_props, '["sdf_discard_mesh_on_save"]', text="Discard Mesh on Save")

    # Auto Smooth Angle setting
    row_smooth_angle = layout.row(align=True)
    row_smooth_angle.label(text="Smooth Angle:")
    row_smooth_angle.prop(obj_for_props, '["sdf_result_auto_smooth_angle"]', text="")

def draw_sdf_group_settings(layout: bpy.types.UILayout, context: bpy.types.Context):
    """ Draws the UI elements for the SDF Group object properties. """
    obj = context.object # Assumes active object is an SDF Group

    is_self_linked, obj_for_props = _draw_link_controls(layout, context, obj, utils.is_sdf_source)

    # --- SDF Type Label (Specific for Group) ---
    label_type_row = layout.row(align=True)
    label_type_row.label(text="SDF Type: Group") # Hardcoded type for clarity

    # --- Processing Order and Hierarchy Buttons (Same as for sources) ---
    if obj.parent:
        hier_row = layout.row(align=True)
        current_order_num_raw = obj.get("sdf_processing_order", 0)
        current_order_display_val = current_order_num_raw
        try:
            current_order_display_val = int(current_order_num_raw / 10)
        except (TypeError, ValueError):
            current_order_display_val = "N/A"
        finally:
            hier_row.label(text=f"Processing Order: {current_order_display_val}")

        can_move_up = False; can_move_down = False
        sdf_siblings = []
        for child in obj.parent.children:
            if child and (utils.is_sdf_source(child) or utils.is_sdf_group(child) or utils.is_sdf_canvas(child)) and \
               child.visible_get(view_layer=context.view_layer):
                sdf_siblings.append(child)
        
        if len(sdf_siblings) > 1:
            def get_sort_key(c):
                return (c.get("sdf_processing_order", float('inf')), c.name)
            sdf_siblings.sort(key=get_sort_key)
            try:
                idx = sdf_siblings.index(obj)
                if idx > 0: can_move_up = True
                if idx < len(sdf_siblings) - 1: can_move_down = True
            except ValueError: pass

        buttons_sub_row = hier_row.row(align=True)
        buttons_sub_row.alignment = 'RIGHT'
        up_button_op_layout = buttons_sub_row.row(align=True)
        up_button_op_layout.active = can_move_up
        op_up = up_button_op_layout.operator(OBJECT_OT_fieldforge_reorder_source.bl_idname, text=" ", icon='TRIA_UP')
        op_up.direction = 'UP'
        down_button_op_layout = buttons_sub_row.row(align=True)
        down_button_op_layout.active = can_move_down
        op_down = down_button_op_layout.operator(OBJECT_OT_fieldforge_reorder_source.bl_idname, text=" ", icon='TRIA_DOWN')
        op_down.direction = 'DOWN'
    
    layout.separator()

    # --- Interaction Mode ---
    row_csg_buttons = layout.row(align=True)
    current_csg_op = obj_for_props.get("sdf_csg_operation", constants.DEFAULT_GROUP_SETTINGS["sdf_csg_operation"])

    op_none = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='RADIOBUT_OFF', depress=(current_csg_op == 'NONE'))
    op_none.csg_mode = 'NONE'
    op_union = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='ADD', depress=(current_csg_op == 'UNION'))
    op_union.csg_mode = 'UNION'
    op_intersect = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='SELECT_INTERSECT', depress=(current_csg_op == 'INTERSECT'))
    op_intersect.csg_mode = 'INTERSECT'
    op_diff = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='SELECT_DIFFERENCE', depress=(current_csg_op == 'DIFFERENCE'))
    op_diff.csg_mode = 'DIFFERENCE'

    # --- Blending ---
    blend_prop_row = layout.row(align=True)
    blend_prop_row.prop(obj_for_props, '["sdf_blend_factor"]', text="Blend Factor")
    blend_prop_row.active = not (obj_for_props.get("sdf_use_morph") or obj_for_props.get("sdf_use_clearance"))

    layout.separator()

    # --- Symmetry Controls ---
    row_symmetry_label = layout.row()
    row_symmetry_label.label(text="Symmetry (Local Axes):")

    row_symmetry_buttons = layout.row(align=True)
    # X Symmetry
    op_symmetry_x = row_symmetry_buttons.operator(
        OBJECT_OT_fieldforge_toggle_group_symmetry.bl_idname,
        text="X",
        depress=obj_for_props.get("sdf_group_symmetry_x", False)
    )
    op_symmetry_x.axis = 'X'

    # Y Symmetry
    op_symmetry_y = row_symmetry_buttons.operator(
        OBJECT_OT_fieldforge_toggle_group_symmetry.bl_idname,
        text="Y",
        depress=obj_for_props.get("sdf_group_symmetry_y", False)
    )
    op_symmetry_y.axis = 'Y'

    # Z Symmetry
    op_symmetry_z = row_symmetry_buttons.operator(
        OBJECT_OT_fieldforge_toggle_group_symmetry.bl_idname,
        text="Z",
        depress=obj_for_props.get("sdf_group_symmetry_z", False)
    )
    op_symmetry_z.axis = 'Z'

    # --- Taper Controls (for taper_xy_z) ---
    taper_label_row = layout.row()
    taper_label_row.label(text="Taper (Axial along Z):")

    row_taper_z = layout.row(align=True)
    is_taper_z_active = obj_for_props.get("sdf_group_taper_z_active", False)

    op_taper_z_toggle = row_taper_z.operator(
        OBJECT_OT_fieldforge_toggle_group_taper_z.bl_idname,
        text="Enable",
        depress=is_taper_z_active
    )

    taper_factor_sub_row = row_taper_z.row(align=True)
    taper_factor_sub_row.active = is_taper_z_active
    taper_factor_sub_row.prop(obj_for_props, '["sdf_group_taper_z_factor"]', text="Factor")

    if is_taper_z_active:
        taper_height_sub_row = layout.row(align=True)
        taper_height_sub_row.active = is_taper_z_active
        taper_height_sub_row.prop(obj_for_props, '["sdf_group_taper_z_height"]', text="Height")
        taper_base_scale_sub_row = layout.row(align=True)
        taper_base_scale_sub_row.active = is_taper_z_active
        taper_base_scale_sub_row.prop(obj_for_props, '["sdf_group_taper_z_base_scale"]', text="Base Scale")

    # --- Shear Controls (Shear X by Y) ---
    shear_label_row = layout.row()
    shear_label_row.label(text="Shear (X by Y):")

    row_shear_x_by_y = layout.row(align=True)
    is_shear_x_by_y_active = obj_for_props.get("sdf_group_shear_x_by_y_active", False)
    
    op_shear_toggle = row_shear_x_by_y.operator(
        OBJECT_OT_fieldforge_toggle_group_shear_x_by_y.bl_idname,
        text="Enable",
        depress=is_shear_x_by_y_active
    )
    
    shear_offset_sub_row = row_shear_x_by_y.row(align=True)
    shear_offset_sub_row.active = is_shear_x_by_y_active
    shear_offset_sub_row.prop(obj_for_props, '["sdf_group_shear_x_by_y_offset"]', text="Offset")

    if is_shear_x_by_y_active:
        shear_base_offset_row = layout.row(align=True)
        shear_base_offset_row.active = is_shear_x_by_y_active
        shear_base_offset_row.prop(obj_for_props, '["sdf_group_shear_x_by_y_base_offset"]', text="Base Offset")
        
        shear_height_row = layout.row(align=True)
        shear_height_row.active = is_shear_x_by_y_active
        shear_height_row.prop(obj_for_props, '["sdf_group_shear_x_by_y_height"]', text="Height (Y)")

    # --- Attract/Repel Controls ---
    attract_repel_label_row = layout.row()
    attract_repel_label_row.label(text="Attract/Repel:")

    row_attract_repel_mode = layout.row(align=True)
    current_ar_mode = obj_for_props.get("sdf_group_attract_repel_mode", 'NONE')
    
    op_ar_none = row_attract_repel_mode.operator(
        OBJECT_OT_fieldforge_set_group_attract_repel_mode.bl_idname,
        text="None", depress=(current_ar_mode == 'NONE'))
    op_ar_none.mode = 'NONE'
    
    op_ar_attract = row_attract_repel_mode.operator(
        OBJECT_OT_fieldforge_set_group_attract_repel_mode.bl_idname,
        text="Attract", depress=(current_ar_mode == 'ATTRACT'))
    op_ar_attract.mode = 'ATTRACT'

    op_ar_repel = row_attract_repel_mode.operator(
        OBJECT_OT_fieldforge_set_group_attract_repel_mode.bl_idname,
        text="Repel", depress=(current_ar_mode == 'REPEL'))
    op_ar_repel.mode = 'REPEL'

    params_active = (current_ar_mode != 'NONE')

    row_ar_params1 = layout.row(align=True)
    row_ar_params1.active = params_active
    row_ar_params1.prop(obj_for_props, '["sdf_group_attract_repel_radius"]', text="Radius")
    row_ar_params1.prop(obj_for_props, '["sdf_group_attract_repel_exaggerate"]', text="Strength")

    row_ar_axes_label = layout.row()
    row_ar_axes_label.active = params_active
    row_ar_axes_label.label(text="Affected Axes:")
    
    row_ar_axes_toggles = layout.row(align=True)
    row_ar_axes_toggles.active = params_active
    row_ar_axes_toggles.prop(obj_for_props, '["sdf_group_attract_repel_axis_x"]', text="X", toggle=True)
    row_ar_axes_toggles.prop(obj_for_props, '["sdf_group_attract_repel_axis_y"]', text="Y", toggle=True)
    row_ar_axes_toggles.prop(obj_for_props, '["sdf_group_attract_repel_axis_z"]', text="Z", toggle=True)

    # --- Twirl Controls ---
    twirl_label_row = layout.row()
    twirl_label_row.label(text="Twirl (Around Axis):")

    row_twirl_enable_axis = layout.row(align=True)
    is_twirl_active = obj_for_props.get("sdf_group_twirl_active", False)
    
    op_twirl_toggle = row_twirl_enable_axis.operator(
        OBJECT_OT_fieldforge_toggle_group_twirl.bl_idname,
        text="Enable",
        depress=is_twirl_active
    )

    # Axis selection buttons (X, Y, Z)
    row_twirl_axis_buttons = row_twirl_enable_axis.row(align=True)
    row_twirl_axis_buttons.active = is_twirl_active # Only enable axis choice if twirl is active
    current_twirl_axis = obj_for_props.get("sdf_group_twirl_axis", 'Z')

    op_twirl_ax = row_twirl_axis_buttons.operator(OBJECT_OT_fieldforge_set_group_twirl_axis.bl_idname, text="X", depress=(current_twirl_axis == 'X'))
    op_twirl_ax.axis = 'X'
    op_twirl_ay = row_twirl_axis_buttons.operator(OBJECT_OT_fieldforge_set_group_twirl_axis.bl_idname, text="Y", depress=(current_twirl_axis == 'Y'))
    op_twirl_ay.axis = 'Y'
    op_twirl_az = row_twirl_axis_buttons.operator(OBJECT_OT_fieldforge_set_group_twirl_axis.bl_idname, text="Z", depress=(current_twirl_axis == 'Z'))
    op_twirl_az.axis = 'Z'
    
    # Parameters: Amount and Radius
    row_twirl_params = layout.row(align=True)
    row_twirl_params.active = is_twirl_active
    row_twirl_params.prop(obj_for_props, '["sdf_group_twirl_amount"]', text="Amount") # Consider subtype='ANGLE' if you want degrees display
    row_twirl_params.prop(obj_for_props, '["sdf_group_twirl_radius"]', text="Radius")

    layout.separator()

    # --- Shell Modifier ---
    shell_controls_row = layout.row(align=True)
    use_shell_val = obj_for_props.get("sdf_use_shell", False)
    shell_controls_row.prop(obj_for_props, '["sdf_use_shell"]', text="Use Shell", toggle=True, icon='MOD_SOLIDIFY')
    
    shell_offset_sub_row = shell_controls_row.row(align=True)
    shell_offset_sub_row.active = use_shell_val
    shell_offset_sub_row.prop(obj_for_props, '["sdf_shell_offset"]', text="Thickness")

    # --- Array Modifier for Group ---
    array_label_row = layout.row(align=True)
    array_label_row.label(text="Array Modifier (Group Content):")
    
    current_main_array_mode = obj_for_props.get("sdf_main_array_mode", 'NONE')
    array_mode_buttons_row = layout.row(align=True)
    op_array_none = array_mode_buttons_row.operator(OBJECT_OT_fieldforge_set_main_array_mode.bl_idname, text="None", depress=(current_main_array_mode == 'NONE'))
    op_array_none.main_mode = 'NONE'
    op_array_linear = array_mode_buttons_row.operator(OBJECT_OT_fieldforge_set_main_array_mode.bl_idname, text="Linear", depress=(current_main_array_mode == 'LINEAR'))
    op_array_linear.main_mode = 'LINEAR'
    op_array_radial = array_mode_buttons_row.operator(OBJECT_OT_fieldforge_set_main_array_mode.bl_idname, text="Radial", depress=(current_main_array_mode == 'RADIAL'))
    op_array_radial.main_mode = 'RADIAL'
    
    layout.separator()

    if current_main_array_mode == 'LINEAR':
        ax_prop = "sdf_array_active_x"; cx_prop = "sdf_array_count_x"
        is_ax_active = obj_for_props.get(ax_prop, False)
        linear_x_row = layout.row(align=True)
        op_toggle_x = linear_x_row.operator(OBJECT_OT_fieldforge_toggle_array_axis.bl_idname, text="X", depress=is_ax_active)
        op_toggle_x.axis = 'X'
        linear_x_params_sub_row = linear_x_row.row(align=True)
        linear_x_params_sub_row.active = is_ax_active
        linear_x_params_sub_row.prop(obj_for_props, f'["{cx_prop}"]', text="Count")

        ay_prop = "sdf_array_active_y"; cy_prop = "sdf_array_count_y"
        is_ay_active = obj_for_props.get(ay_prop, False)
        linear_y_row = layout.row(align=True)
        linear_y_row.active = is_ax_active
        op_toggle_y = linear_y_row.operator(OBJECT_OT_fieldforge_toggle_array_axis.bl_idname, text="Y", depress=is_ay_active)
        op_toggle_y.axis = 'Y'
        linear_y_params_sub_row = linear_y_row.row(align=True)
        linear_y_params_sub_row.active = is_ay_active
        linear_y_params_sub_row.prop(obj_for_props, f'["{cy_prop}"]', text="Count")

        az_prop = "sdf_array_active_z"; cz_prop = "sdf_array_count_z"
        is_az_active = obj_for_props.get(az_prop, False)
        linear_z_row = layout.row(align=True)
        linear_z_row.active = is_ay_active
        op_toggle_z = linear_z_row.operator(OBJECT_OT_fieldforge_toggle_array_axis.bl_idname, text="Z", depress=is_az_active)
        op_toggle_z.axis = 'Z'
        linear_z_params_sub_row = linear_z_row.row(align=True)
        linear_z_params_sub_row.active = is_az_active
        linear_z_params_sub_row.prop(obj_for_props, f'["{cz_prop}"]', text="Count")

    elif current_main_array_mode == 'RADIAL':
        radial_count_row = layout.row(align=True)
        radial_count_row.prop(obj_for_props, '["sdf_radial_count"]', text="Count")

def draw_sdf_source_info(layout: bpy.types.UILayout, context: bpy.types.Context):
    """ Draws the UI elements for the SDF Source object properties. """
    obj = context.object
    sdf_type = obj.get("sdf_type", "Unknown")

    is_obj_itself_linked, obj_for_props = _draw_link_controls(layout, context, obj, utils.is_sdf_source)

    parent_obj = obj_for_props.parent
    parent_is_canvas = False
    if parent_obj and parent_obj.get(constants.SDF_CANVAS_MARKER, False) and sdf_type in constants._2D_SHAPE_TYPES:
        parent_is_canvas = True

    # --- SDF Type Label ---
    label_type_row = layout.row(align=True)
    if parent_is_canvas:
        label_type_row.label(text="(Canvas Element)", icon='INFO')
    else:
        label_type_row.label(text=f"SDF Type: {sdf_type.capitalize()}")

    # --- Processing Order and Hierarchy Buttons ---
    if obj.parent:
        hier_row = layout.row(align=True)
        current_order_num_raw = obj.get("sdf_processing_order", 0)
        current_order_display_val = current_order_num_raw
        
        order_label = "Processing Order (3D):"
        if parent_is_canvas:
            order_label = "Order in Canvas (2D):"
        hier_row.label(text=f"{order_label} {current_order_display_val}")

        can_move_up = False; can_move_down = False
        sdf_siblings_for_reorder = []

        for sibling_candidate in obj.parent.children: 
            if not (sibling_candidate and sibling_candidate.visible_get(view_layer=context.view_layer)):
                continue

            is_relevant_sibling = False
            if parent_is_canvas:
                if utils.is_sdf_source(sibling_candidate) and \
                sibling_candidate.get("sdf_type") in constants._2D_SHAPE_TYPES:
                    is_relevant_sibling = True
            else:
                if utils.is_sdf_source(sibling_candidate) or \
                utils.is_sdf_group(sibling_candidate) or \
                sibling_candidate.get(constants.SDF_CANVAS_MARKER, False):
                    is_relevant_sibling = True
            
            if is_relevant_sibling:
                sdf_siblings_for_reorder.append(sibling_candidate)
        
        if len(sdf_siblings_for_reorder) > 1:
            def get_sort_key_panel(c): return (c.get("sdf_processing_order", float('inf')), c.name)
            sdf_siblings_for_reorder.sort(key=get_sort_key_panel)
            try:
                idx = sdf_siblings_for_reorder.index(obj)
                if idx > 0: can_move_up = True
                if idx < len(sdf_siblings_for_reorder) - 1: can_move_down = True
            except ValueError: pass

        buttons_sub_row = hier_row.row(align=True)
        buttons_sub_row.alignment = 'RIGHT'

        up_button_op_layout = buttons_sub_row.row(align=True)
        up_button_op_layout.active = can_move_up
        op_up = up_button_op_layout.operator(OBJECT_OT_fieldforge_reorder_source.bl_idname, text=" ", icon='TRIA_UP')
        op_up.direction = 'UP'

        down_button_op_layout = buttons_sub_row.row(align=True)
        down_button_op_layout.active = can_move_down
        op_down = down_button_op_layout.operator(OBJECT_OT_fieldforge_reorder_source.bl_idname, text=" ", icon='TRIA_DOWN')
        op_down.direction = 'DOWN'

    layout.separator()

    ## --- Interaction Mode (CSG with parent/canvas) ---
    row_csg_buttons = layout.row(align=True)
    current_csg_op = obj_for_props.get("sdf_csg_operation", constants.DEFAULT_SOURCE_SETTINGS["sdf_csg_operation"])

    op_none = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='RADIOBUT_OFF', depress=(current_csg_op == 'NONE'))
    op_none.csg_mode = 'NONE'
    op_union = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='ADD', depress=(current_csg_op == 'UNION'))
    op_union.csg_mode = 'UNION'
    op_intersect = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='SELECT_INTERSECT', depress=(current_csg_op == 'INTERSECT'))
    op_intersect.csg_mode = 'INTERSECT'
    op_diff = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='SELECT_DIFFERENCE', depress=(current_csg_op == 'DIFFERENCE'))
    op_diff.csg_mode = 'DIFFERENCE'

    # --- Blending ---
    blend_row = layout.row(align=True)
    blend_row.prop(obj_for_props, '["sdf_blend_factor"]', text="Blend Factor")

    use_loft = obj_for_props.get("sdf_use_loft", False)
    use_morph = obj_for_props.get("sdf_use_morph", False) and not use_loft
    use_clearance = obj_for_props.get("sdf_use_clearance", False) and not use_loft and not use_morph
    csg_active = not use_loft and not use_morph and not use_clearance

    col_3d_mods = layout.column()
    col_3d_mods.active = not parent_is_canvas # Disable this whole column if parent is canvas

    if sdf_type in constants._2D_SHAPE_TYPES: # Loft only for 2D types
        row_loft_toggle = col_3d_mods.row(align=True)
        row_loft_toggle.prop(obj_for_props, '["sdf_use_loft"]', text="Use Loft", toggle=True, icon='IPO_LINEAR')
        
    row_morph_controls = col_3d_mods.row(align=True)
    row_morph_controls.active = not use_loft
    row_morph_controls.prop(obj_for_props, '["sdf_use_morph"]', text="Morph", toggle=True, icon='MOD_SIMPLEDEFORM')
    morph_factor_sub_row = row_morph_controls.row(align=True)
    morph_factor_sub_row.active = obj_for_props.get("sdf_use_morph", False) and not use_loft # Active if morph is on and loft off
    morph_factor_sub_row.prop(obj_for_props, '["sdf_morph_factor"]', text="Factor")
    
    row_clearance_controls = layout.row(align=True)
    row_clearance_controls.active = not use_loft and not use_morph
    row_clearance_controls.prop(obj_for_props, '["sdf_use_clearance"]', text="Clearance", toggle=True, icon='MOD_OFFSET')
    clearance_offset_sub_row = row_clearance_controls.row(align=True)
    clearance_offset_sub_row.active = obj_for_props.get("sdf_use_clearance", False) and not use_loft and not use_morph
    clearance_offset_sub_row.prop(obj_for_props, '["sdf_clearance_offset"]', text="Offset")

    if obj_for_props.get("sdf_use_clearance", False) and not obj_for_props.get("sdf_use_loft", False) and not obj_for_props.get("sdf_use_morph", False):
        row_clearance_keep_orig = col_3d_mods.row(align=True) # Still under col_3d_mods
        row_clearance_keep_orig.prop(obj_for_props, '["sdf_clearance_keep_original"]', text="Keep Original Shape")

    col_3d_mods.separator() # Separator within the 3D mods block

    # Extrusion Depth (for the source itself, if it's 2D and NOT under a Canvas)
    if sdf_type in constants._2D_SHAPE_TYPES: # Check if it's a type that *can* be extruded
        extrude_row_source = col_3d_mods.row(align=True)
        extrude_row_source.prop(obj_for_props, '["sdf_extrusion_depth"]', text="Self Extrusion Depth")
        col_3d_mods.separator()
    
    layout.separator()

    # --- Shape Parameters ---
    has_params_drawn = False

    if sdf_type == "text":
        text_param_row = layout.row(align=True)
        text_param_row.prop(obj_for_props, '["sdf_text_string"]', text="Text")
        text_extrude_row = layout.row(align=True)
        text_extrude_row.prop(obj_for_props, '["sdf_extrusion_depth"]', text="Extrusion Depth")
        has_params_drawn = True
    elif sdf_type == "rounded_box":
        round_radius_row = layout.row(align=True)
        round_radius_row.prop(obj_for_props, '["sdf_round_radius"]', text="Rounding Radius")
        has_params_drawn = True
    elif sdf_type == "torus":
        torus_major_row = layout.row(align=True)
        torus_major_row.prop(obj_for_props, '["sdf_torus_major_radius"]', text="Major Radius")
        torus_minor_row = layout.row(align=True)
        torus_minor_row.prop(obj_for_props, '["sdf_torus_minor_radius"]', text="Minor Radius")
        
        maj_r = obj_for_props.get("sdf_torus_major_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_torus_major_radius"])
        min_r = obj_for_props.get("sdf_torus_minor_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_torus_minor_radius"])
        if min_r >= maj_r:
            error_row = layout.row(align=True)
            error_row.label(text="Minor radius should be < Major", icon='ERROR')
        has_params_drawn = True
    elif sdf_type in {"circle", "ring", "polygon"}:
        if sdf_type == "ring":
            ring_inner_row = layout.row(align=True)
            ring_inner_row.prop(obj_for_props, '["sdf_inner_radius"]', text="Inner Radius (Unit)")
            has_params_drawn = True
        elif sdf_type == "polygon":
            poly_sides_row = layout.row(align=True)
            poly_sides_row.prop(obj_for_props, '["sdf_sides"]', text="Sides")
            has_params_drawn = True
        extrude_2d_row = layout.row(align=True)
        extrude_2d_row.prop(obj_for_props, '["sdf_extrusion_depth"]', text="Extrusion Depth")
        if not has_params_drawn and sdf_type == "circle":
             has_params_drawn = True
            
    elif sdf_type == "half_space":
        info_row = layout.row(align=True)
        info_row.label(text="Parameters: Defined by Transform")
        has_params_drawn = True

    if not has_params_drawn:
        if sdf_type not in ["cube", "sphere", "cylinder", "cone", "pyramid"]:
            no_param_row = layout.row(align=True)
            no_param_row.label(text="Parameters: None (Defined by Transform)")
        elif sdf_type in ["cube", "sphere", "cylinder", "cone", "pyramid"]:
            basic_no_param_row = layout.row(align=True)
            basic_no_param_row.label(text="Parameters: None (Defined by Transform)")

    layout.separator()

    # --- Shell Modifier ---
    shell_controls_row = layout.row(align=True)
    use_shell_val = obj_for_props.get("sdf_use_shell", False)
    shell_controls_row.prop(obj_for_props, '["sdf_use_shell"]', text="Use Shell", toggle=True, icon='MOD_SOLIDIFY')
    
    shell_offset_sub_row = shell_controls_row.row(align=True)
    shell_offset_sub_row.active = use_shell_val
    shell_offset_sub_row.prop(obj_for_props, '["sdf_shell_offset"]', text="Thickness")

def draw_sdf_canvas_settings(layout: bpy.types.UILayout, context: bpy.types.Context):
    obj = context.object # Assumes active object is the Canvas

    # Draw link controls at the top
    is_self_linked, obj_for_props = _draw_link_controls(layout, context, obj, utils.is_sdf_source)

    label_type_row = layout.row(align=True)
    label_type_row.label(text="SDF Type: 2D Canvas")

    if obj_for_props.parent: 
        hier_row = layout.row(align=True)
        current_order_num_raw = obj_for_props.get("sdf_processing_order",0); current_order_display_val = current_order_num_raw
        try: current_order_display_val = int(current_order_num_raw/10)
        except (TypeError,ValueError): current_order_display_val = "N/A"
        finally: hier_row.label(text=f"Processing Order (3D): {current_order_display_val}")
        can_move_up=False; can_move_down=False; sdf_siblings=[]
        for child in obj_for_props.parent.children:
            if child and (utils.is_sdf_source(child) or utils.is_sdf_group(child) or utils.is_sdf_canvas(child)) and child.visible_get(view_layer=context.view_layer): sdf_siblings.append(child)
        if len(sdf_siblings)>1:
            def get_sort_key(c): return (c.get("sdf_processing_order",float('inf')),c.name)
            sdf_siblings.sort(key=get_sort_key)
            try: idx=sdf_siblings.index(obj_for_props);
            except ValueError: pass
            else:
                if idx>0: can_move_up=True
                if idx<len(sdf_siblings)-1: can_move_down=True
        buttons_sub_row=hier_row.row(align=True); buttons_sub_row.alignment='RIGHT'
        up_button_op_layout=buttons_sub_row.row(align=True); up_button_op_layout.active=can_move_up
        op_up=up_button_op_layout.operator(OBJECT_OT_fieldforge_reorder_source.bl_idname,text=" ",icon='TRIA_UP'); op_up.direction='UP'
        down_button_op_layout=buttons_sub_row.row(align=True); down_button_op_layout.active=can_move_down
        op_down=down_button_op_layout.operator(OBJECT_OT_fieldforge_reorder_source.bl_idname,text=" ",icon='TRIA_DOWN'); op_down.direction='DOWN'

    layout.separator()

    # CSG operation of this Canvas (as an extruded 3D object) with its parent
    row_csg_buttons = layout.row(align=True)
    current_csg_op = obj_for_props.get("sdf_csg_operation", constants.DEFAULT_CANVAS_SETTINGS["sdf_csg_operation"])

    op_none = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='RADIOBUT_OFF', depress=(current_csg_op == 'NONE'))
    op_none.csg_mode = 'NONE'
    op_union = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='ADD', depress=(current_csg_op == 'UNION'))
    op_union.csg_mode = 'UNION'
    op_intersect = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='SELECT_INTERSECT', depress=(current_csg_op == 'INTERSECT'))
    op_intersect.csg_mode = 'INTERSECT'
    op_diff = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='SELECT_DIFFERENCE', depress=(current_csg_op == 'DIFFERENCE'))
    op_diff.csg_mode = 'DIFFERENCE'
    
    layout.prop(obj_for_props, '["sdf_blend_factor"]', text="Blend factor")
    layout.separator()

    row_blend = layout.row(align=True)
    row_blend.prop(obj_for_props, '["sdf_blend_factor"]', text="Blend factor")
    
    layout.separator()

    layout.label(text="3D Output:")
    use_revolve = obj_for_props.get("sdf_canvas_use_revolve", False)

    row_output_method = layout.row(align=True)

    extrude_op_layout = row_output_method.row(align=True)
    extrude_op_layout.active = use_revolve
    op_extrude_toggle = extrude_op_layout.operator(
        OBJECT_OT_fieldforge_toggle_canvas_revolve.bl_idname,
        text="Extrude",
        depress=not use_revolve
    )
    revolve_op_layout = row_output_method.row(align=True)
    revolve_op_layout.active = not use_revolve
    op_revolve_toggle = revolve_op_layout.operator(
        OBJECT_OT_fieldforge_toggle_canvas_revolve.bl_idname,
        text="Revolve (Y-axis)",
        depress=use_revolve
    )

    extrude_depth_row = layout.row(align=True)
    extrude_depth_row.active = not use_revolve
    extrude_depth_row.prop(obj_for_props, '["sdf_extrusion_depth"]', text="Extrusion Depth")

    if use_revolve:
        revolve_params_row = layout.row(align=True)
        revolve_params_row.active = use_revolve
        revolve_params_row.label(text="Profile: Local Positive X")
    layout.separator()



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
        return utils.lf is not None # Only show panel if libfive is available

    def draw_header(self, context): # Add a header to the panel
        layout = self.layout
        obj = context.object
        header_text = "FieldForge"
        icon = 'NONE'

        if obj:
            if obj.get(constants.SDF_BOUNDS_MARKER, False):
                header_text = f"Bounds: {obj.name}"
                icon = 'MOD_BUILD'
            elif utils.is_sdf_group(obj): # Check for group
                header_text = f"Group: {obj.name}"
                icon = 'GROUP' # Or 'OUTLINER_OB_GROUP_INSTANCE'
            elif obj.get(constants.SDF_CANVAS_MARKER, False):
                header_text = f"Canvas: {obj.name}"; icon = 'OUTLINER_OB_SURFACE'
            elif utils.is_sdf_source(obj):
                header_text = f"Source: {obj.name}"
                icon = 'OBJECT_DATA' # Or more specific based on sdf_type if desired
        layout.label(text=header_text, icon=icon)


    def draw(self, context):
        layout = self.layout
        obj = context.object

        if utils.lf is None: # Should be caught by poll, but good safety
            layout.label(text="libfive library not found!", icon='ERROR')
            layout.separator()
            layout.label(text="Check Blender Console.")
            return

        if not obj:
            layout.label(text="Select a FieldForge object.", icon='INFO')
            return

        # Check object type and call appropriate drawing function
        if obj.get(constants.SDF_BOUNDS_MARKER, False):
            draw_sdf_bounds_settings(layout, context)
        elif utils.is_sdf_group(obj):
            draw_sdf_group_settings(layout, context)
        elif obj.get(constants.SDF_CANVAS_MARKER, False):
            draw_sdf_canvas_settings(layout, context)
        elif utils.is_sdf_source(obj):
            draw_sdf_source_info(layout, context)
        else:
            layout.label(text="Not a FieldForge object.", icon='QUESTION')


# --- List of Panel Classes to Register ---
classes_to_register = (
    VIEW3D_PT_fieldforge_main,
)