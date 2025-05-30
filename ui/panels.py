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
    OBJECT_OT_fieldforge_toggle_group_reflection,
    OBJECT_OT_fieldforge_toggle_group_symmetry,
    OBJECT_OT_fieldforge_toggle_group_taper_z,
    OBJECT_OT_fieldforge_toggle_group_shear_x_by_y,
    OBJECT_OT_fieldforge_set_group_attract_repel_mode,
    OBJECT_OT_fieldforge_toggle_group_twirl,
    OBJECT_OT_fieldforge_set_group_twirl_axis,
)

# --- UI Drawing Helper Functions ---

def draw_sdf_bounds_settings(layout: bpy.types.UILayout, context: bpy.types.Context):
    """ Draws the UI elements for the Bounds object settings. """
    obj = context.object # Assumes the active object IS the Bounds object

    # --- Resolution and Update Controls ---
    row_res_label = layout.row(align=True)
    row_res_label.label(text="Resolution:")
    row_res_props = layout.row(align=True)
    row_res_props.prop(obj, '["sdf_viewport_resolution"]', text="")
    row_res_props.prop(obj, '["sdf_auto_update"]', text="Auto Update", toggle=True)

    row_update_controls = layout.row(align=True)
    row_update_controls.prop(obj, '["sdf_final_resolution"]', text="")
    row_update_controls.operator(OBJECT_OT_sdf_manual_update.bl_idname, text="Manual Update", icon='FILE_REFRESH')

    layout.separator()

    # --- Update Timing ---
    row_timing_label = layout.row()
    row_timing_label.label(text="Update Timing:")
    
    row_timing_props = layout.row(align=True)
    row_timing_props.prop(obj, '["sdf_realtime_update_delay"]', text="Inactive Delay")
    row_timing_props.prop(obj, '["sdf_minimum_update_interval"]', text="Min Interval")

    layout.separator()

    # --- Display and Save Options ---
    row_display_options = layout.row()
    row_display_options.label(text="Options:")

    row_display_options = layout.row(align=True)
    row_display_options.prop(obj, '["sdf_show_source_empties"]', text="Show Visuals", toggle=True)
    row_display_options.prop(obj, '["sdf_create_result_object"]', text="Recreate Mesh", toggle=True)
    row_display_options.prop(obj, '["sdf_discard_mesh_on_save"]', text="Discard on Save", toggle=True)

def draw_sdf_group_settings(layout: bpy.types.UILayout, context: bpy.types.Context):
    """ Draws the UI elements for the SDF Group object properties. """
    obj = context.object # Assumes active object is an SDF Group

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
            if child and (utils.is_sdf_source(child) or utils.is_sdf_group(child)) and \
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

    # --- Child Object Blending ---
    child_blend_prop_row = layout.row(align=True)
    child_blend_prop_row.prop(obj, '["sdf_child_blend_factor"]', text="Blend Factor")

    layout.separator() # Added separator

    # --- Reflection Controls ---
    row_reflect_label = layout.row()
    row_reflect_label.label(text="Reflection (Local Axes):")

    row_reflect_buttons = layout.row(align=True)
    # X Reflection
    op_reflect_x = row_reflect_buttons.operator(
        OBJECT_OT_fieldforge_toggle_group_reflection.bl_idname,
        text="X",
        depress=obj.get("sdf_group_reflect_x", False)
    )
    op_reflect_x.axis = 'X'

    # Y Reflection
    op_reflect_y = row_reflect_buttons.operator(
        OBJECT_OT_fieldforge_toggle_group_reflection.bl_idname,
        text="Y",
        depress=obj.get("sdf_group_reflect_y", False)
    )
    op_reflect_y.axis = 'Y'

    # Z Reflection
    op_reflect_z = row_reflect_buttons.operator(
        OBJECT_OT_fieldforge_toggle_group_reflection.bl_idname,
        text="Z",
        depress=obj.get("sdf_group_reflect_z", False)
    )
    op_reflect_z.axis = 'Z'

    layout.separator()

    # --- Symmetry Controls ---
    row_symmetry_label = layout.row()
    row_symmetry_label.label(text="Symmetry (Local Axes):")

    row_symmetry_buttons = layout.row(align=True)
    # X Symmetry
    op_symmetry_x = row_symmetry_buttons.operator(
        OBJECT_OT_fieldforge_toggle_group_symmetry.bl_idname,
        text="X",
        depress=obj.get("sdf_group_symmetry_x", False)
    )
    op_symmetry_x.axis = 'X'

    # Y Symmetry
    op_symmetry_y = row_symmetry_buttons.operator(
        OBJECT_OT_fieldforge_toggle_group_symmetry.bl_idname,
        text="Y",
        depress=obj.get("sdf_group_symmetry_y", False)
    )
    op_symmetry_y.axis = 'Y'

    # Z Symmetry
    op_symmetry_z = row_symmetry_buttons.operator(
        OBJECT_OT_fieldforge_toggle_group_symmetry.bl_idname,
        text="Z",
        depress=obj.get("sdf_group_symmetry_z", False)
    )
    op_symmetry_z.axis = 'Z'

    # --- Taper Controls (for taper_xy_z) ---
    taper_label_row = layout.row()
    taper_label_row.label(text="Taper (Axial along Z):")

    row_taper_z = layout.row(align=True)
    is_taper_z_active = obj.get("sdf_group_taper_z_active", False)

    op_taper_z_toggle = row_taper_z.operator(
        OBJECT_OT_fieldforge_toggle_group_taper_z.bl_idname,
        text="Enable",
        depress=is_taper_z_active
    )

    taper_factor_sub_row = row_taper_z.row(align=True)
    taper_factor_sub_row.active = is_taper_z_active
    taper_factor_sub_row.prop(obj, '["sdf_group_taper_z_factor"]', text="Factor")

    if is_taper_z_active:
        taper_height_sub_row = layout.row(align=True)
        taper_height_sub_row.active = is_taper_z_active
        taper_height_sub_row.prop(obj, '["sdf_group_taper_z_height"]', text="Height")
        taper_base_scale_sub_row = layout.row(align=True)
        taper_base_scale_sub_row.active = is_taper_z_active
        taper_base_scale_sub_row.prop(obj, '["sdf_group_taper_z_base_scale"]', text="Base Scale")

    # --- Shear Controls (Shear X by Y) ---
    shear_label_row = layout.row()
    shear_label_row.label(text="Shear (X by Y):")

    row_shear_x_by_y = layout.row(align=True)
    is_shear_x_by_y_active = obj.get("sdf_group_shear_x_by_y_active", False)
    
    op_shear_toggle = row_shear_x_by_y.operator(
        OBJECT_OT_fieldforge_toggle_group_shear_x_by_y.bl_idname,
        text="Enable",
        depress=is_shear_x_by_y_active
    )
    
    shear_offset_sub_row = row_shear_x_by_y.row(align=True)
    shear_offset_sub_row.active = is_shear_x_by_y_active
    shear_offset_sub_row.prop(obj, '["sdf_group_shear_x_by_y_offset"]', text="Offset")

    if is_shear_x_by_y_active:
        shear_base_offset_row = layout.row(align=True)
        shear_base_offset_row.active = is_shear_x_by_y_active
        shear_base_offset_row.prop(obj, '["sdf_group_shear_x_by_y_base_offset"]', text="Base Offset")
        
        shear_height_row = layout.row(align=True)
        shear_height_row.active = is_shear_x_by_y_active
        shear_height_row.prop(obj, '["sdf_group_shear_x_by_y_height"]', text="Height (Y)")

    # --- Attract/Repel Controls ---
    attract_repel_label_row = layout.row()
    attract_repel_label_row.label(text="Attract/Repel:")

    row_attract_repel_mode = layout.row(align=True)
    current_ar_mode = obj.get("sdf_group_attract_repel_mode", 'NONE')
    
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
    row_ar_params1.prop(obj, '["sdf_group_attract_repel_radius"]', text="Radius")
    row_ar_params1.prop(obj, '["sdf_group_attract_repel_exaggerate"]', text="Strength")

    row_ar_axes_label = layout.row()
    row_ar_axes_label.active = params_active
    row_ar_axes_label.label(text="Affected Axes:")
    
    row_ar_axes_toggles = layout.row(align=True)
    row_ar_axes_toggles.active = params_active
    row_ar_axes_toggles.prop(obj, '["sdf_group_attract_repel_axis_x"]', text="X", toggle=True)
    row_ar_axes_toggles.prop(obj, '["sdf_group_attract_repel_axis_y"]', text="Y", toggle=True)
    row_ar_axes_toggles.prop(obj, '["sdf_group_attract_repel_axis_z"]', text="Z", toggle=True)

    # --- Twirl Controls ---
    twirl_label_row = layout.row()
    twirl_label_row.label(text="Twirl (Around Axis):")

    row_twirl_enable_axis = layout.row(align=True)
    is_twirl_active = obj.get("sdf_group_twirl_active", False)
    
    op_twirl_toggle = row_twirl_enable_axis.operator(
        OBJECT_OT_fieldforge_toggle_group_twirl.bl_idname,
        text="Enable",
        depress=is_twirl_active
    )

    # Axis selection buttons (X, Y, Z)
    row_twirl_axis_buttons = row_twirl_enable_axis.row(align=True)
    row_twirl_axis_buttons.active = is_twirl_active # Only enable axis choice if twirl is active
    current_twirl_axis = obj.get("sdf_group_twirl_axis", 'Z')

    op_twirl_ax = row_twirl_axis_buttons.operator(OBJECT_OT_fieldforge_set_group_twirl_axis.bl_idname, text="X", depress=(current_twirl_axis == 'X'))
    op_twirl_ax.axis = 'X'
    op_twirl_ay = row_twirl_axis_buttons.operator(OBJECT_OT_fieldforge_set_group_twirl_axis.bl_idname, text="Y", depress=(current_twirl_axis == 'Y'))
    op_twirl_ay.axis = 'Y'
    op_twirl_az = row_twirl_axis_buttons.operator(OBJECT_OT_fieldforge_set_group_twirl_axis.bl_idname, text="Z", depress=(current_twirl_axis == 'Z'))
    op_twirl_az.axis = 'Z'
    
    # Parameters: Amount and Radius
    row_twirl_params = layout.row(align=True)
    row_twirl_params.active = is_twirl_active
    row_twirl_params.prop(obj, '["sdf_group_twirl_amount"]', text="Amount") # Consider subtype='ANGLE' if you want degrees display
    row_twirl_params.prop(obj, '["sdf_group_twirl_radius"]', text="Radius")

def draw_sdf_source_info(layout: bpy.types.UILayout, context: bpy.types.Context):
    """ Draws the UI elements for the SDF Source object properties. """
    obj = context.object
    sdf_type = obj.get("sdf_type", "Unknown")

    # --- SDF Type Label ---
    label_type_row = layout.row(align=True)
    label_type_row.label(text=f"SDF Type: {sdf_type.capitalize()}")

    # --- Processing Order and Hierarchy Buttons ---
    if obj.parent:
        hier_row = layout.row(align=True)
        
        # Label for processing order
        current_order_num_raw = obj.get("sdf_processing_order", 0)
        current_order_display_val = current_order_num_raw
        try:
            current_order_display_val = int(current_order_num_raw / 10)
        except (TypeError, ValueError):
            current_order_display_val = "N/A"
        finally:
            hier_row.label(text=f"Processing Order: {current_order_display_val}")

        # Determine if Up/Down buttons should be active
        can_move_up = False
        can_move_down = False
        sdf_siblings = []
        for child in obj.parent.children:
            if child and (utils.is_sdf_source(child) or utils.is_sdf_group(child)) and \
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
    interact_label_row = layout.row(align=True)
    interact_label_row.label(text="Interaction Mode:")

    use_loft = obj.get("sdf_use_loft", False)
    use_morph = obj.get("sdf_use_morph", False) and not use_loft
    use_clearance = obj.get("sdf_use_clearance", False) and not use_loft and not use_morph
    csg_active = not use_loft and not use_morph and not use_clearance
    
    row_csg_buttons = layout.row(align=True)
    row_csg_buttons.active = csg_active
    current_csg_op = obj.get("sdf_csg_operation", constants.DEFAULT_SOURCE_SETTINGS["sdf_csg_operation"])

    op_none = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='RADIOBUT_OFF', depress=(current_csg_op == 'NONE'))
    op_none.csg_mode = 'NONE'
    op_union = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='ADD', depress=(current_csg_op == 'UNION'))
    op_union.csg_mode = 'UNION'
    op_intersect = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='SELECT_INTERSECT', depress=(current_csg_op == 'INTERSECT'))
    op_intersect.csg_mode = 'INTERSECT'
    op_diff = row_csg_buttons.operator(OBJECT_OT_fieldforge_set_csg_mode.bl_idname, text="    ", icon='SELECT_DIFFERENCE', depress=(current_csg_op == 'DIFFERENCE'))
    op_diff.csg_mode = 'DIFFERENCE'


    # Loft Toggle - only for relevant 2D shapes (as per original addon logic)
    if sdf_type in ["circle", "polygon", "ring", "text"]:
        row_loft_toggle = layout.row(align=True)
        row_loft_toggle.prop(obj, '["sdf_use_loft"]', text="Use Loft", toggle=True, icon='IPO_LINEAR')
        
    row_morph_controls = layout.row(align=True)
    row_morph_controls.active = not use_loft
    row_morph_controls.prop(obj, '["sdf_use_morph"]', text="Morph", toggle=True, icon='MOD_SIMPLEDEFORM')
    morph_factor_sub_row = row_morph_controls.row(align=True)
    morph_factor_sub_row.active = obj.get("sdf_use_morph", False) and not use_loft # Active if morph is on and loft off
    morph_factor_sub_row.prop(obj, '["sdf_morph_factor"]', text="Factor")
    
    row_clearance_controls = layout.row(align=True)
    row_clearance_controls.active = not use_loft and not use_morph
    row_clearance_controls.prop(obj, '["sdf_use_clearance"]', text="Clearance", toggle=True, icon='MOD_OFFSET')
    clearance_offset_sub_row = row_clearance_controls.row(align=True)
    clearance_offset_sub_row.active = obj.get("sdf_use_clearance", False) and not use_loft and not use_morph
    clearance_offset_sub_row.prop(obj, '["sdf_clearance_offset"]', text="Offset")

    if use_clearance and not use_loft and not use_morph:
        row_clearance_keep_orig = layout.row(align=True)
        row_clearance_keep_orig.prop(obj, '["sdf_clearance_keep_original"]', text="Keep Original Shape")
    
    layout.separator()

    # --- Shape Parameters ---
    has_params_drawn = False

    if sdf_type == "text":
        text_param_row = layout.row(align=True)
        text_param_row.prop(obj, '["sdf_text_string"]', text="Text")
        text_extrude_row = layout.row(align=True)
        text_extrude_row.prop(obj, '["sdf_extrusion_depth"]', text="Extrusion Depth")
        has_params_drawn = True
    elif sdf_type == "rounded_box":
        round_radius_row = layout.row(align=True)
        round_radius_row.prop(obj, '["sdf_round_radius"]', text="Rounding Radius")
        has_params_drawn = True
    elif sdf_type == "torus":
        torus_major_row = layout.row(align=True)
        torus_major_row.prop(obj, '["sdf_torus_major_radius"]', text="Major Radius")
        torus_minor_row = layout.row(align=True)
        torus_minor_row.prop(obj, '["sdf_torus_minor_radius"]', text="Minor Radius")
        
        maj_r = obj.get("sdf_torus_major_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_torus_major_radius"])
        min_r = obj.get("sdf_torus_minor_radius", constants.DEFAULT_SOURCE_SETTINGS["sdf_torus_minor_radius"])
        if min_r >= maj_r:
            error_row = layout.row(align=True)
            error_row.label(text="Minor radius should be < Major", icon='ERROR')
        has_params_drawn = True
    elif sdf_type in {"circle", "ring", "polygon"}:
        if sdf_type == "ring":
            ring_inner_row = layout.row(align=True)
            ring_inner_row.prop(obj, '["sdf_inner_radius"]', text="Inner Radius (Unit)")
            has_params_drawn = True
        elif sdf_type == "polygon":
            poly_sides_row = layout.row(align=True)
            poly_sides_row.prop(obj, '["sdf_sides"]', text="Sides")
            has_params_drawn = True
        extrude_2d_row = layout.row(align=True)
        extrude_2d_row.prop(obj, '["sdf_extrusion_depth"]', text="Extrusion Depth")
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
    use_shell_val = obj.get("sdf_use_shell", False)
    shell_controls_row.prop(obj, '["sdf_use_shell"]', text="Use Shell", toggle=True, icon='MOD_SOLIDIFY')
    
    shell_offset_sub_row = shell_controls_row.row(align=True)
    shell_offset_sub_row.active = use_shell_val
    shell_offset_sub_row.prop(obj, '["sdf_shell_offset"]', text="Thickness")
    
    layout.separator()

    # --- Child Object Blending ---
    child_blend_row = layout.row(align=True)
    child_blend_row.active = not use_clearance
    child_blend_row.prop(obj, '["sdf_child_blend_factor"]', text="Blend Factor")

    layout.separator()

    # --- Array Modifier ---
    array_label_row = layout.row(align=True)
    array_label_row.label(text="Array Modifier:")
    
    current_main_array_mode = obj.get("sdf_main_array_mode", 'NONE')
    array_mode_buttons_row = layout.row(align=True)
    op_array_none = array_mode_buttons_row.operator(OBJECT_OT_fieldforge_set_main_array_mode.bl_idname, text="None", depress=(current_main_array_mode == 'NONE'))
    op_array_none.main_mode = 'NONE'
    op_array_linear = array_mode_buttons_row.operator(OBJECT_OT_fieldforge_set_main_array_mode.bl_idname, text="Linear", depress=(current_main_array_mode == 'LINEAR'))
    op_array_linear.main_mode = 'LINEAR'
    op_array_radial = array_mode_buttons_row.operator(OBJECT_OT_fieldforge_set_main_array_mode.bl_idname, text="Radial", depress=(current_main_array_mode == 'RADIAL'))
    op_array_radial.main_mode = 'RADIAL'
    
    layout.separator()

    if current_main_array_mode == 'LINEAR':
        # Each axis gets its own primary row in the new layout
        ax_prop = "sdf_array_active_x"; dx_prop = "sdf_array_delta_x"; cx_prop = "sdf_array_count_x"
        is_ax_active = obj.get(ax_prop, False)
        linear_x_row = layout.row(align=True)
        op_toggle_x = linear_x_row.operator(OBJECT_OT_fieldforge_toggle_array_axis.bl_idname, text="X", depress=is_ax_active)
        op_toggle_x.axis = 'X'
        linear_x_params_sub_row = linear_x_row.row(align=True)
        linear_x_params_sub_row.active = is_ax_active
        linear_x_params_sub_row.prop(obj, f'["{dx_prop}"]', text="Delta")
        linear_x_params_sub_row.prop(obj, f'["{cx_prop}"]', text="Count")

        ay_prop = "sdf_array_active_y"; dy_prop = "sdf_array_delta_y"; cy_prop = "sdf_array_count_y"
        is_ay_active = obj.get(ay_prop, False)
        linear_y_row = layout.row(align=True)
        linear_y_row.active = is_ax_active
        op_toggle_y = linear_y_row.operator(OBJECT_OT_fieldforge_toggle_array_axis.bl_idname, text="Y", depress=is_ay_active)
        op_toggle_y.axis = 'Y'
        linear_y_params_sub_row = linear_y_row.row(align=True)
        linear_y_params_sub_row.active = is_ay_active
        linear_y_params_sub_row.prop(obj, f'["{dy_prop}"]', text="Delta")
        linear_y_params_sub_row.prop(obj, f'["{cy_prop}"]', text="Count")

        az_prop = "sdf_array_active_z"; dz_prop = "sdf_array_delta_z"; cz_prop = "sdf_array_count_z"
        is_az_active = obj.get(az_prop, False)
        linear_z_row = layout.row(align=True)
        linear_z_row.active = is_ay_active
        op_toggle_z = linear_z_row.operator(OBJECT_OT_fieldforge_toggle_array_axis.bl_idname, text="Z", depress=is_az_active)
        op_toggle_z.axis = 'Z'
        linear_z_params_sub_row = linear_z_row.row(align=True)
        linear_z_params_sub_row.active = is_az_active
        linear_z_params_sub_row.prop(obj, f'["{dz_prop}"]', text="Delta")
        linear_z_params_sub_row.prop(obj, f'["{cz_prop}"]', text="Count")

    elif current_main_array_mode == 'RADIAL':
        radial_count_row = layout.row(align=True)
        radial_count_row.prop(obj, '["sdf_radial_count"]', text="Count")
        
        radial_center_row = layout.row(align=True)
        radial_center_row.prop(obj, '["sdf_radial_center"]', text="Center Offset")
        
    if current_main_array_mode != 'NONE':
        array_center_origin_row = layout.row(align=True)
        array_center_origin_row.prop(obj, '["sdf_array_center_on_origin"]', text="Center on Origin")


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
        elif utils.is_sdf_source(obj):
            draw_sdf_source_info(layout, context)
        else:
            layout.label(text="Not a FieldForge object.", icon='QUESTION')


# --- List of Panel Classes to Register ---
classes_to_register = (
    VIEW3D_PT_fieldforge_main,
)