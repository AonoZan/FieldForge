# FieldForge/ui/operators.py

"""
Defines all Blender Operator classes for the FieldForge addon.
Includes operators for adding bounds/sources, manual updates, UI interactions,
and the modal selection/grab handler.
"""

import bpy
import traceback # For modal error reporting
from bpy.props import (
    FloatVectorProperty, FloatProperty, IntProperty, PointerProperty,
    StringProperty, EnumProperty, BoolProperty
)
from bpy.types import Operator
from bpy_extras import view3d_utils
from mathutils import Vector, Matrix

# Use relative imports assuming this file is in FieldForge/ui/
try:
    from .. import constants
    from .. import utils # For general helpers
    from ..core import state as ff_state # Alias state module
    from ..core import update_manager as ff_update # Alias update manager
    from ..drawing import tag_redraw_all_view3d, _draw_line_data_read # Import redraw utility and READ buffer for picking
except ImportError:
    # Fallback for running script directly - replace with actual paths/names if needed
    print("FieldForge WARN (operators.py): Could not perform relative imports. Using placeholders.")
    import constants
    import utils
    from core import state as ff_state
    from core import update_manager as ff_update
    from drawing import tag_redraw_all_view3d, _draw_line_data_read # Might fail if not run as addon

# --- Global State (for Modal Operator) ---
# Flag managed by the modal operator itself and register/unregister
_selection_handler_running = False


# --- Add Bounds Operator ---

class OBJECT_OT_add_sdf_bounds(Operator):
    """Adds a new SDF Bounds controller Empty and prepares its result mesh setup"""
    bl_idname = "object.add_sdf_bounds"
    bl_label = "Add SDF Bounds Controller"
    bl_options = {'REGISTER', 'UNDO'}

    location: FloatVectorProperty(
        name="Location",
        default=(0.0, 0.0, 0.0),
        subtype='TRANSLATION',
        description="Initial location for the Bounds object"
    )
    bounds_name_prefix: StringProperty(
        name="Name Prefix",
        default="SDF_System",
        description="Prefix used for naming the Bounds and Result objects"
    )

    def make_unique_name(self, context, base_name):
        """ Generates a unique object name based on the base name. """
        if base_name not in context.scene.objects:
            return base_name
        i = 1
        while f"{base_name}.{i:03d}" in context.scene.objects:
            i += 1
        return f"{base_name}.{i:03d}"

    def execute(self, context):
        # Create Bounds Empty
        # Use context override to ensure placement if called from different context
        with context.temp_override(window=context.window, area=context.area, region=context.region):
             bpy.ops.object.empty_add(type='CUBE', radius=1.0, location=self.location)
        bounds_obj = context.active_object
        if not bounds_obj:
            self.report({'ERROR'}, "Failed to create Bounds Empty object.")
            return {'CANCELLED'}

        unique_bounds_name = self.make_unique_name(context, self.bounds_name_prefix + "_Bounds")
        bounds_obj.name = unique_bounds_name

        # Initial setup
        bounds_obj.scale = (2.0, 2.0, 2.0)
        bounds_obj.empty_display_size = 1.0
        bounds_obj.color = (0.2, 0.8, 1.0, 1.0) # Distinctive blue color
        bounds_obj.hide_render = True # Controller doesn't need rendering

        # Set markers and properties using constants
        bounds_obj[constants.SDF_BOUNDS_MARKER] = True
        result_name_base = self.bounds_name_prefix + "_Result"
        final_result_name = self.make_unique_name(context, result_name_base)
        bounds_obj[constants.SDF_RESULT_OBJ_NAME_PROP] = final_result_name

        # Store Default Settings from constants.py
        for key, value in constants.DEFAULT_SETTINGS.items():
            try:
                bounds_obj[key] = value
            except TypeError as e:
                print(f"FieldForge WARN: Could not set default property '{key}' on {bounds_obj.name}: {e}. Value: {value}")

        self.report({'INFO'}, f"Added SDF Bounds: {bounds_obj.name}")

        # Select only the new Bounds object
        context.view_layer.objects.active = bounds_obj
        for obj in context.selected_objects:
            if obj != bounds_obj: # Avoid deselecting the object itself if it was already selected
                obj.select_set(False)
        bounds_obj.select_set(True)


        # Trigger initial update check using function from update_manager
        if utils.lf is not None: # Only schedule if libfive seems available
            try:
                # Use timer to ensure object is fully integrated
                bpy.app.timers.register(
                    # Use lambda to pass current scene context correctly
                    lambda scn=context.scene, name=bounds_obj.name: ff_update.check_and_trigger_update(scn, name, "add_bounds"),
                    first_interval=0.01 # Short delay
                )
            except Exception as e:
                 print(f"FieldForge ERROR: Failed to schedule initial check for {bounds_obj.name}: {e}")

        tag_redraw_all_view3d() # Force redraw
        return {'FINISHED'}


# --- Add Source Base Operator ---

class AddSdfSourceBase(Operator):
    """Base class for adding various SDF source type Empties"""
    bl_options = {'REGISTER', 'UNDO'}

    # Common interaction properties shown in dialog
    initial_child_blend: FloatProperty(name="Child Blend Factor", description="Blend factor for children parented TO this object", default=0.0, min=0.0, max=5.0, subtype='FACTOR')
    is_negative: BoolProperty(name="Negative (Subtractive)", description="Make this shape subtractive", default=False)
    use_clearance: BoolProperty(name="Use Clearance", description="Make shape subtract an offset version (exclusive with Negative/Morph)", default=False)
    initial_clearance_offset: FloatProperty(name="Clearance Offset", description="Offset distance for Clearance", default=0.05, min=0.0, subtype='DISTANCE', unit='LENGTH')
    use_morph: BoolProperty(name="Use Morph", description="Morph from parent towards this shape (exclusive with Negative/Clearance)", default=False)
    initial_morph_factor: FloatProperty(name="Morph Factor", description="Morph amount (0=parent, 1=this)", default=0.5, min=0.0, max=1.0, subtype='FACTOR')

    @classmethod
    def poll(cls, context):
        # Check libfive availability via utils.lf check
        active_obj = context.active_object
        return utils.lf is not None and active_obj is not None and \
               (active_obj.get(constants.SDF_BOUNDS_MARKER, False) or utils.find_parent_bounds(active_obj) is not None)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self) # Show options

    def make_unique_name(self, context, base_name):
        """ Generates a unique object name. """
        if base_name not in context.scene.objects: return base_name
        i = 1; unique_name = f"{base_name}.{i:03d}"
        while unique_name in context.scene.objects: i += 1; unique_name = f"{base_name}.{i:03d}"
        return unique_name

    def add_sdf_empty(self, context, sdf_type, display_type, name_prefix, props_to_set=None):
        """ Helper method to create and configure the SDF source Empty """
        target_parent = context.active_object
        parent_bounds = utils.find_parent_bounds(target_parent)
        if not parent_bounds and target_parent.get(constants.SDF_BOUNDS_MARKER, False): parent_bounds = target_parent
        if not parent_bounds: self.report({'ERROR'}, "Active object not part of SDF hierarchy."); return {'CANCELLED'}

        # Create Empty at cursor, handle potential context issues
        try:
            with context.temp_override(window=context.window, area=context.area, region=context.region):
                 bpy.ops.object.empty_add(type=display_type, radius=0, location=context.scene.cursor.location, scale=(1.0, 1.0, 1.0))
            obj = context.active_object
            if not obj: raise RuntimeError("Failed to get active object after empty_add.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to add Empty: {e}")
            return {'CANCELLED'}

        obj.name = self.make_unique_name(context, name_prefix)
        obj.parent = target_parent
        try: obj.matrix_parent_inverse = target_parent.matrix_world.inverted()
        except ValueError: obj.matrix_parent_inverse.identity(); print(f"WARN: Could not invert parent matrix for {target_parent.name}.")

        # --- Assign Standard SDF & Interaction Properties ---
        obj[constants.SDF_PROPERTY_MARKER] = True
        obj["sdf_type"] = sdf_type
        defaults = constants.DEFAULT_SOURCE_SETTINGS # Use defaults from constants
        obj["sdf_child_blend_factor"] = self.initial_child_blend # Set from operator prop
        # Determine initial interaction mode (exclusive toggles)
        final_use_morph = self.use_morph
        final_use_clearance = self.use_clearance and not final_use_morph
        final_is_negative = self.is_negative and not final_use_clearance and not final_use_morph
        obj["sdf_use_morph"] = final_use_morph; obj["sdf_use_clearance"] = final_use_clearance; obj["sdf_is_negative"] = final_is_negative
        obj["sdf_morph_factor"] = self.initial_morph_factor if final_use_morph else defaults["sdf_morph_factor"]
        obj["sdf_clearance_offset"] = self.initial_clearance_offset if final_use_clearance else defaults["sdf_clearance_offset"]
        obj["sdf_clearance_keep_original"] = defaults["sdf_clearance_keep_original"]
        # Set other defaults from constants file
        obj["sdf_use_loft"] = defaults["sdf_use_loft"]; obj["sdf_use_shell"] = defaults["sdf_use_shell"]
        obj["sdf_shell_offset"] = defaults["sdf_shell_offset"]; obj["sdf_main_array_mode"] = defaults["sdf_main_array_mode"]
        obj["sdf_array_active_x"] = defaults["sdf_array_active_x"]; obj["sdf_array_count_x"] = defaults["sdf_array_count_x"]; obj["sdf_array_delta_x"] = defaults["sdf_array_delta_x"]
        obj["sdf_array_active_y"] = defaults["sdf_array_active_y"]; obj["sdf_array_count_y"] = defaults["sdf_array_count_y"]; obj["sdf_array_delta_y"] = defaults["sdf_array_delta_y"]
        obj["sdf_array_active_z"] = defaults["sdf_array_active_z"]; obj["sdf_array_count_z"] = defaults["sdf_array_count_z"]; obj["sdf_array_delta_z"] = defaults["sdf_array_delta_z"]
        obj["sdf_radial_count"] = defaults["sdf_radial_count"]; obj["sdf_radial_center"] = tuple(defaults["sdf_radial_center"]) # Ensure tuple

        # --- Assign Type-Specific Properties (Passed via props_to_set) ---
        if props_to_set:
            for key, value in props_to_set.items():
                try: obj[key] = value
                except TypeError as e: print(f"WARN: Could not set prop '{key}' on {obj.name}: {e}. Value: {value}")

        # Set color based on PRIMARY interaction mode
        if final_use_morph: obj.color = (0.3, 0.5, 1.0, 1.0) # Blueish
        elif final_use_clearance or final_is_negative: obj.color = (1.0, 0.3, 0.3, 1.0) # Reddish
        else: obj.color = (0.5, 0.5, 0.5, 1.0) # Neutral grey

        # Set initial STANDARD visibility based on PARENT BOUNDS setting
        show_standard_empty = utils.get_bounds_setting(parent_bounds, "sdf_show_source_empties")
        obj.hide_viewport = not show_standard_empty
        obj.hide_render = not show_standard_empty # Keep consistent

        # Select the new object
        context.view_layer.objects.active = obj
        for sel_obj in context.selected_objects:
            if sel_obj != obj: sel_obj.select_set(False)
        obj.select_set(True)

        # Report success
        mode_str = " [Morph]" if final_use_morph else " [Clearance]" if final_use_clearance else " [Negative]" if final_is_negative else ""
        self.report({'INFO'}, f"Added SDF Source: {obj.name} ({sdf_type}) under {target_parent.name}{mode_str}")

        # Trigger update check for the PARENT BOUNDS hierarchy
        ff_update.check_and_trigger_update(context.scene, parent_bounds.name, f"add_{sdf_type}_source")
        tag_redraw_all_view3d() # Redraw to show new object/outline
        return {'FINISHED'}


# --- Concrete Add Source Operators ---
# (These inherit from AddSdfSourceBase and mainly define bl_idname, bl_label,
#  and potentially specific properties for their invoke dialog)

class OBJECT_OT_add_sdf_cube_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Cube"""
    bl_idname = "object.add_sdf_cube_source"; bl_label = "SDF Cube Source"
    def execute(self, context): return self.add_sdf_empty(context, "cube", 'PLAIN_AXES', "FF_Cube")

class OBJECT_OT_add_sdf_sphere_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Sphere"""
    bl_idname = "object.add_sdf_sphere_source"; bl_label = "SDF Sphere Source"
    def execute(self, context): return self.add_sdf_empty(context, "sphere", 'PLAIN_AXES', "FF_Sphere")

class OBJECT_OT_add_sdf_cylinder_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Cylinder"""
    bl_idname = "object.add_sdf_cylinder_source"; bl_label = "SDF Cylinder Source"
    def execute(self, context): return self.add_sdf_empty(context, "cylinder", 'PLAIN_AXES', "FF_Cylinder")

class OBJECT_OT_add_sdf_cone_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Cone"""
    bl_idname = "object.add_sdf_cone_source"; bl_label = "SDF Cone Source"
    def execute(self, context): return self.add_sdf_empty(context, "cone", 'PLAIN_AXES', "FF_Cone")

class OBJECT_OT_add_sdf_torus_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Torus"""
    bl_idname = "object.add_sdf_torus_source"; bl_label = "SDF Torus Source"
    initial_major_radius: FloatProperty(name="Major Radius (Unit)", default=constants.DEFAULT_SOURCE_SETTINGS["sdf_torus_major_radius"], min=0.01, description="Radius from center to tube center")
    initial_minor_radius: FloatProperty(name="Minor Radius (Unit)", default=constants.DEFAULT_SOURCE_SETTINGS["sdf_torus_minor_radius"], min=0.005, description="Radius of the tube")
    def execute(self, context):
        major_r = self.initial_major_radius; minor_r = min(self.initial_minor_radius, major_r - 0.001)
        props = {"sdf_torus_major_radius": major_r, "sdf_torus_minor_radius": minor_r}
        return self.add_sdf_empty(context, "torus", 'PLAIN_AXES', "FF_Torus", props_to_set=props)

class OBJECT_OT_add_sdf_rounded_box_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Rounded Box"""
    bl_idname = "object.add_sdf_rounded_box_source"; bl_label = "SDF Rounded Box Source"
    initial_round_radius: FloatProperty(name="Rounding Radius (Unit)", default=constants.DEFAULT_SOURCE_SETTINGS["sdf_round_radius"], min=0.0, max=0.5, description="Corner radius relative to unit size")
    def execute(self, context):
        props = {"sdf_round_radius": self.initial_round_radius}
        return self.add_sdf_empty(context, "rounded_box", 'PLAIN_AXES', "FF_RoundedBox", props_to_set=props)

class OBJECT_OT_add_sdf_circle_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Circle (Extruded)"""
    bl_idname = "object.add_sdf_circle_source"; bl_label = "SDF Circle Source"
    initial_extrusion_depth: FloatProperty(name="Extrusion Depth", default=constants.DEFAULT_SOURCE_SETTINGS["sdf_extrusion_depth"], min=0.001, subtype='DISTANCE', description="Depth of extrusion along local Z")
    def execute(self, context):
        props = {"sdf_extrusion_depth": self.initial_extrusion_depth}
        return self.add_sdf_empty(context, "circle", 'PLAIN_AXES', "FF_Circle", props_to_set=props)

class OBJECT_OT_add_sdf_ring_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Ring (Extruded)"""
    bl_idname = "object.add_sdf_ring_source"; bl_label = "SDF Ring Source"
    initial_inner_radius: FloatProperty(name="Inner Radius (Unit)", default=constants.DEFAULT_SOURCE_SETTINGS["sdf_inner_radius"], min=0.0, max=0.499, description="Inner radius relative to unit outer radius (0.5)")
    initial_extrusion_depth: FloatProperty(name="Extrusion Depth", default=constants.DEFAULT_SOURCE_SETTINGS["sdf_extrusion_depth"], min=0.001, subtype='DISTANCE', description="Depth of extrusion along local Z")
    def execute(self, context):
        props = {"sdf_inner_radius": self.initial_inner_radius, "sdf_extrusion_depth": self.initial_extrusion_depth}
        return self.add_sdf_empty(context, "ring", 'PLAIN_AXES', "FF_Ring", props_to_set=props)

class OBJECT_OT_add_sdf_polygon_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Polygon (Extruded)"""
    bl_idname = "object.add_sdf_polygon_source"; bl_label = "SDF Polygon Source"
    initial_sides: IntProperty(name="Number of Sides", default=constants.DEFAULT_SOURCE_SETTINGS["sdf_sides"], min=3, max=64, description="Number of polygon sides")
    initial_extrusion_depth: FloatProperty(name="Extrusion Depth", default=constants.DEFAULT_SOURCE_SETTINGS["sdf_extrusion_depth"], min=0.001, subtype='DISTANCE', description="Depth of extrusion along local Z")
    def execute(self, context):
        props = {"sdf_sides": self.initial_sides, "sdf_extrusion_depth": self.initial_extrusion_depth}
        return self.add_sdf_empty(context, "polygon", 'PLAIN_AXES', "FF_Polygon", props_to_set=props)

class OBJECT_OT_add_sdf_half_space_source(AddSdfSourceBase):
    """Adds an Empty controller for an SDF Half Space"""
    bl_idname = "object.add_sdf_half_space_source"; bl_label = "SDF Half Space Source"
    def execute(self, context): return self.add_sdf_empty(context, "half_space", 'PLAIN_AXES', "FF_HalfSpace")


# --- Manual Update Operator ---

class OBJECT_OT_sdf_manual_update(Operator):
    """Manually triggers a high-resolution FINAL update for the ACTIVE SDF Bounds hierarchy."""
    bl_idname = "object.sdf_manual_update"; bl_label = "Update Final SDF Now"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return utils.lf is not None and obj and obj.get(constants.SDF_BOUNDS_MARKER, False)

    def execute(self, context):
        bounds_obj = context.active_object; bounds_name = bounds_obj.name
        print(f"FieldForge: Manual final update triggered for {bounds_name}.")
        ff_update.cancel_debounce_timer(bounds_name) # Use update_manager function
        if bounds_name in ff_update._last_trigger_states: ff_update._last_trigger_states.pop(bounds_name)
        if ff_update._updates_pending.get(bounds_name, False):
             self.report({'WARNING'}, f"Update already in progress for {bounds_name}."); return {'CANCELLED'}
        current_state = ff_state.get_current_sdf_state(context, bounds_obj) # Use state module function
        if not current_state: self.report({'ERROR'}, f"Failed get state for {bounds_name}."); return {'CANCELLED'}
        ff_update._updates_pending[bounds_name] = True
        try:
            bpy.app.timers.register(lambda scn=context.scene, name=bounds_name, state=current_state: ff_update.run_sdf_update(scn, name, state, is_viewport_update=False), first_interval=0.0)
        except Exception as e: print(f"ERROR: Reg FINAL update timer: {e}"); ff_update._updates_pending[bounds_name] = False; self.report({'ERROR'}, f"Failed schedule update."); return {'CANCELLED'}
        self.report({'INFO'}, f"Scheduled final update for {bounds_name}."); return {'FINISHED'}


# --- UI Interaction Operators ---

class OBJECT_OT_fieldforge_toggle_array_axis(Operator):
    """Toggles the activation state of a specific FieldForge array axis."""
    bl_idname = "object.fieldforge_toggle_array_axis"; bl_label = "Toggle Array Axis"
    bl_options = {'REGISTER', 'UNDO'}
    axis: EnumProperty(items=[('X',"X","X"), ('Y',"Y","Y"), ('Z',"Z","Z")], name="Axis", default='X')
    @classmethod
    def poll(cls, context): return context.active_object and utils.is_sdf_source(context.active_object)
    def execute(self, context):
        obj = context.active_object; act_x="sdf_array_active_x"; act_y="sdf_array_active_y"; act_z="sdf_array_active_z"
        is_x=obj.get(act_x,False); is_y=obj.get(act_y,False); is_z=obj.get(act_z,False); changed = False
        if self.axis == 'X':
            new_x = not is_x; obj[act_x]=new_x; changed=True
            if not new_x:
                if is_y: obj[act_y]=False; changed=True
                if is_z: obj[act_z]=False; changed=True
        elif self.axis == 'Y':
            if not is_x: self.report({'WARNING'}, "Activate X axis first to enable Y."); return {'CANCELLED'}
            new_y = not is_y; obj[act_y]=new_y; changed=True
            if not new_y and is_z: obj[act_z]=False; changed=True
        elif self.axis == 'Z':
            if not is_x or not is_y: self.report({'WARNING'}, "Activate X and Y axes first to enable Z."); return {'CANCELLED'}
            obj[act_z] = not is_z; changed=True
        if changed:
            parent_bounds = utils.find_parent_bounds(obj)
            if parent_bounds: ff_update.check_and_trigger_update(context.scene, parent_bounds.name, f"toggle_array_{obj.name}_{self.axis}")
            tag_redraw_all_view3d()
        return {'FINISHED'}

class OBJECT_OT_fieldforge_set_main_array_mode(Operator):
    """Sets the main array mode custom property."""
    bl_idname = "object.fieldforge_set_main_array_mode"; bl_label = "Set Main Array Mode"
    bl_options = {'REGISTER', 'UNDO'}
    main_mode: EnumProperty(items=[('NONE',"None","None"), ('LINEAR',"Linear","Linear"), ('RADIAL',"Radial","Radial")], name="Main Array Mode", default='NONE')
    @classmethod
    def poll(cls, context): return context.active_object and utils.is_sdf_source(context.active_object)
    def execute(self, context): # Logic remains the same, just uses utils/ff_update
        obj = context.active_object; prop_name = "sdf_main_array_mode"
        current_mode = obj.get(prop_name, 'NONE'); changed = False
        if current_mode != self.main_mode:
            obj[prop_name] = self.main_mode; changed = True
            if current_mode == 'LINEAR' or self.main_mode == 'NONE': # Reset linear flags
                if obj.get("sdf_array_active_x", False): obj["sdf_array_active_x"]=False; changed=True
                if obj.get("sdf_array_active_y", False): obj["sdf_array_active_y"]=False; changed=True
                if obj.get("sdf_array_active_z", False): obj["sdf_array_active_z"]=False; changed=True
        if changed:
            parent_bounds = utils.find_parent_bounds(obj)
            if parent_bounds: ff_update.check_and_trigger_update(context.scene, parent_bounds.name, f"set_main_array_{obj.name}_{self.main_mode}")
            tag_redraw_all_view3d()
        return {'FINISHED'}


# --- Modal Selection/Grab Handler ---

class VIEW3D_OT_fieldforge_select_handler(Operator):
    """Modal operator: Selection and MANUAL grab for FieldForge visuals."""
    bl_idname = "view3d.fieldforge_select_handler"; bl_label = "FieldForge Select Handler"
    bl_options = {'REGISTER', 'INTERNAL'} # Keep INTERNAL

    _timer = None # Timer for addon status checks

    # --- State Tracking Variables ---
    selecting_mouse_button: str = 'LEFTMOUSE'
    is_button_down: bool = False
    drag_threshold_squared: int = 9
    is_manually_grabbing: bool = False # Changed from drag_initiated for clarity
    target_obj_found_on_press: bool = False
    target_obj_on_press: bpy.types.Object = None # Store ref to object being dragged
    initial_mouse_screen_pos: tuple = (0, 0)
    initial_mouse_region_pos: tuple = (0, 0)
    initial_world_matrix: Matrix = None
    initial_grab_depth: float = 0.0
    grab_region: bpy.types.Region = None
    grab_region_data: bpy.types.RegionView3D = None

    def modal(self, context, event):
    # print(f"FF Modal Tick: Event {event.type} {event.value} | ButtonDown: {self.is_button_down} | Grabbing: {self.is_manually_grabbing}") # DEBUG

    # --- Addon Status / Global Flag Checks ---
        prefs = getattr(context, 'preferences', None); addons = getattr(prefs, 'addons', None) if prefs else None
        if not addons or "FieldForge" not in addons: return self.cancel_modal(context)
        if not _selection_handler_running: return self.cancel_modal(context)

        # --- If MANUALLY GRABBING ---
        if self.is_manually_grabbing:
            # Handle Confirm / Cancel / Move during grab
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':    # Confirm Grab
                # print("--- GRAB CONFIRM (Left Click) ---") # DEBUG
                self.reset_drag_state(); tag_redraw_all_view3d(); return {'RUNNING_MODAL'}
            elif event.type == 'RIGHTMOUSE' and event.value == 'PRESS': # Cancel Grab
                # print("--- GRAB CANCEL (Right Click) ---") # DEBUG
                self.restore_initial_matrix(); self.reset_drag_state(); tag_redraw_all_view3d(); return {'RUNNING_MODAL'}
            elif event.type == 'ESC' and event.value == 'PRESS':        # Cancel Grab (ESC)
                # print("--- GRAB CANCEL (ESC) ---") # DEBUG
                self.restore_initial_matrix(); self.reset_drag_state(); tag_redraw_all_view3d(); return {'RUNNING_MODAL'}
            elif event.type == 'MOUSEMOVE':                            # Update Grab Position
                # print("--- GRAB MOVE ---") # DEBUG
                self.update_grab_position(context, event); return {'PASS_THROUGH'} # Pass for redraw
            elif event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'MIDDLEMOUSE'}: # Allow navigation
                return {'PASS_THROUGH'}
            return {'RUNNING_MODAL'} # Consume other events

        # --- If NOT grabbing ---
        else:
            # --- Handle Press Event (Stores state for potential click/drag) ---
            if event.type == self.selecting_mouse_button and event.value == 'PRESS' and not self.is_button_down:
                self.reset_drag_state(); self.is_button_down = True; self.initial_mouse_screen_pos = (event.mouse_x, event.mouse_y)
                # print(f"--- PRESS --- Screen=({self.initial_mouse_screen_pos}). ButtonDown SET TRUE.") # DEBUG
                if event.alt: self.reset_drag_state(); return {'PASS_THROUGH'}
                area, region, region_data, click_x, click_y = self.find_context_under_mouse(context, event.mouse_x, event.mouse_y)
                self.initial_mouse_region_pos = (click_x, click_y) if click_x >= 0 else (-1, -1)
                if area and region and region_data and click_x >= 0:
                    found_name = self.find_object_under_cursor(context, region, region_data, click_x, click_y)
                    # print(f"--- PRESS --- Found object: {found_name}") # DEBUG
                    if found_name:
                        target_ref = context.scene.objects.get(found_name)
                        if target_ref:
                            self.perform_selection(context, event, target_ref) # Select/activate now
                            self.target_obj_found_on_press = True; self.target_obj_on_press = context.view_layer.objects.active
                            if self.target_obj_on_press: # Store context if selection resulted in active obj
                                self.grab_region = region; self.grab_region_data = region_data; self.initial_world_matrix = self.target_obj_on_press.matrix_world.copy()
                                # --- Calculate initial grab depth ---
                                origin_loc = self.target_obj_on_press.matrix_world.translation
                                origin_screen = view3d_utils.location_3d_to_region_2d(region, region_data, origin_loc)
                                if origin_screen:
                                    try:
                                        loc3d = view3d_utils.region_2d_to_location_3d(region, region_data, self.initial_mouse_region_pos, origin_loc)
                                        self.initial_grab_depth = loc3d.z
                                    except (RuntimeError, ValueError):
                                        self.initial_grab_depth = (region_data.view_location - origin_loc).length
                                else:
                                    self.initial_grab_depth = (region_data.view_location - origin_loc).length
                                # print(f"--- PRESS --- Selected {self.target_obj_on_press.name}. Depth: {self.initial_grab_depth:.2f}. State set.") # DEBUG
                                tag_redraw_all_view3d(); return {'RUNNING_MODAL'} # Wait for CLICK/CLICK_DRAG/RELEASE
                    # else:
                    #     print("--- PRESS --- No object found under cursor.") # DEBUG
                # else:
                    # print("--- PRESS --- Invalid region/area. Area: {area}, Region: {region}, RegionData: {region_data}, Click: ({click_x}, {click_y})") # DEBUG
                self.reset_drag_state()
                # print("--- PRESS --- No target/invalid region. Reset state. PASS_THROUGH.") # DEBUG
                return {'PASS_THROUGH'}

            # --- Handle CLICK Event (Indicates a click finished without drag) ---
            elif event.type == self.selecting_mouse_button and event.value == 'CLICK':
                # print(f"--- CLICK --- Detected. ButtonDown: {self.is_button_down}. Consuming.") # DEBUG
                if self.is_button_down and not self.is_manually_grabbing:
                    return {'RUNNING_MODAL'}
                return {'PASS_THROUGH'}

            # --- Handle CLICK_DRAG Event (Initiates Manual Grab) ---
            elif event.type == self.selecting_mouse_button and event.value == 'CLICK_DRAG':
                # print(f"--- CLICK_DRAG --- Detected. Checking conditions...") # DEBUG
                # print(f"    is_button_down: {self.is_button_down}")
                # print(f"    target_obj_found_on_press: {self.target_obj_found_on_press}")
                # print(f"    target_obj_on_press: {self.target_obj_on_press.name if self.target_obj_on_press else None}")
                # print(f"    initial_world_matrix: {self.initial_world_matrix is not None}")
                # print(f"    is_manually_grabbing: {self.is_manually_grabbing}")
                if self.is_button_down and self.target_obj_found_on_press and self.target_obj_on_press and \
                self.initial_world_matrix and not self.is_manually_grabbing:
                    print(f"--- CLICK_DRAG --- Initiating manual grab for {self.target_obj_on_press.name}") # DEBUG
                    self.is_manually_grabbing = True
                    # bpy.ops.ed.undo_push(message="Start FieldForge Grab")
                    self.update_grab_position(context, event)
                    tag_redraw_all_view3d()
                #     return {'RUNNING_MODAL'}
                # else:
                #     print(f"--- CLICK_DRAG --- Conditions FAILED. Passing through.") # DEBUG
                return {'PASS_THROUGH'}

            # --- Fallback: Manual Drag Detection via MOUSEMOVE ---
            elif event.type == 'MOUSEMOVE' and self.is_button_down and not self.is_manually_grabbing:
                # Check if mouse moved beyond drag threshold
                dx = event.mouse_x - self.initial_mouse_screen_pos[0]
                dy = event.mouse_y - self.initial_mouse_screen_pos[1]
                dist_sq = dx * dx + dy * dy
                if dist_sq > self.drag_threshold_squared and self.target_obj_found_on_press and self.target_obj_on_press and self.initial_world_matrix:
                    # print(f"--- MOUSEMOVE DRAG --- Initiating manual grab for {self.target_obj_on_press.name}. Dist: {dist_sq**.5:.2f}") # DEBUG
                    self.is_manually_grabbing = True
                    self.update_grab_position(context, event)
                    tag_redraw_all_view3d()
                    return {'PASS_THROUGH'}
                return {'PASS_THROUGH'}

            # --- Handle Release Event ---
            elif event.type == self.selecting_mouse_button and event.value == 'RELEASE':
                print(f"--- RELEASE --- ButtonDown: {self.is_button_down}") # DEBUG
                if self.is_button_down:
                    self.reset_drag_state()
                    return {'RUNNING_MODAL'}
                return {'PASS_THROUGH'}

            elif event.type in {'DEL', 'X', 'ESC'} and event.value == 'PRESS':
                if self.is_manually_grabbing:
                    if self.initial_world_matrix and self.target_obj_on_press:
                        try: self.target_obj_on_press.matrix_world = self.initial_world_matrix
                        except ReferenceError: pass
                    self.reset_drag_state()
                    tag_redraw_all_view3d()
                return {'PASS_THROUGH'}

            # --- Pass through other events ---
            return {'PASS_THROUGH'}

    # --- Helper: Restore Initial Matrix ---
    # (Keep implementation from previous answer)
    def restore_initial_matrix(self):
        if self.is_manually_grabbing and self.initial_world_matrix and self.target_obj_on_press:
            try:
                if self.target_obj_on_press.name in bpy.context.scene.objects: self.target_obj_on_press.matrix_world = self.initial_world_matrix
                else: print("WARN: Target object for matrix restore no longer exists.")
            except ReferenceError: pass
            except Exception as e: print(f"ERROR restoring matrix: {e}")


    # --- Helper: Update Grab Position ---
    # (Keep implementation from previous answer)
    def update_grab_position(self, context, event):
        if not (self.target_obj_on_press and self.grab_region and self.grab_region_data and self.initial_world_matrix):
            print("DRAGGING WARN: Missing target/context/matrix for update."); self.reset_drag_state(); return
        try:
            current_region_pos = (event.mouse_x - self.grab_region.x, event.mouse_y - self.grab_region.y)
            # Use initial matrix translation as the reference point for projection depth
            ref_point = self.initial_world_matrix.translation
            init_3d = view3d_utils.region_2d_to_location_3d(self.grab_region, self.grab_region_data, self.initial_mouse_region_pos, ref_point)
            curr_3d = view3d_utils.region_2d_to_location_3d(self.grab_region, self.grab_region_data, current_region_pos, init_3d if init_3d else ref_point) # Use init_3d as depth ref if valid
            if init_3d and curr_3d:
                 delta = curr_3d - init_3d; new_matrix = Matrix.Translation(delta) @ self.initial_world_matrix
                 self.target_obj_on_press.matrix_world = new_matrix
            # else: print("DRAGGING WARN: Projection failed.") # DEBUG
        except Exception as e_drag: print(f"ERROR manual drag update: {e_drag}"); self.reset_drag_state()

    def reset_drag_state(self):
        """Resets all state variables related to dragging AND clicking."""
        #print("--- STATE RESET ---") # DEBUG
        self.is_button_down = False
        self.is_manually_grabbing = False
        # Reset others too for good measure
        self.target_obj_found_on_press = False
        self.target_obj_on_press = None
        self.initial_mouse_screen_pos = (-1, -1)
        self.initial_mouse_region_pos = (-1, -1)
        self.initial_world_matrix = None
        self.initial_grab_depth = 0.0
        self.grab_region = None
        self.grab_region_data = None

    def find_context_under_mouse(self, context, screen_x, screen_y):
        """ Finds Area, Region, Region3D, and calculates region coords under screen coords. """
        area_under_mouse=None; region_under_mouse=None; region_data_under_mouse=None
        space_data=None; click_region_x=-1; click_region_y=-1
        screen = getattr(context.window, 'screen', getattr(context, 'screen', None))
        if not screen: return area_under_mouse, region_under_mouse, region_data_under_mouse, click_region_x, click_region_y
        for area_iter in getattr(screen, 'areas', []):
            if (area_iter.x <= screen_x < area_iter.x + area_iter.width and area_iter.y <= screen_y < area_iter.y + area_iter.height):
                if area_iter.type == 'VIEW_3D':
                    area_under_mouse = area_iter; space_data = getattr(area_iter, 'spaces', {}).active
                    for region_iter in getattr(area_iter, 'regions', []):
                        if region_iter.type == 'WINDOW':
                            if (region_iter.x <= screen_x < region_iter.x + region_iter.width and region_iter.y <= screen_y < region_iter.y + region_iter.height):
                                region_under_mouse = region_iter; click_region_x = screen_x - region_iter.x; click_region_y = screen_y - region_iter.y; break
                    if region_under_mouse and space_data and hasattr(space_data, 'region_3d'): region_data_under_mouse = space_data.region_3d
                    break
        return area_under_mouse, region_under_mouse, region_data_under_mouse, click_region_x, click_region_y

    def find_object_under_cursor(self, context, region, region_data, mouse_region_x, mouse_region_y, threshold=10.0):
        """ Finds object using data stored on WindowManager. """
        wm = getattr(context, 'window_manager', None)
        # --->>> Get data from Window Manager property <<<---
        draw_data = wm.get("fieldforge_draw_data", {}) if wm else {}

        mx, my = mouse_region_x, mouse_region_y
        if not region or not region_data: return None
        min_dist_sq = (threshold * context.preferences.system.pixel_size)**2
        closest_obj_name = None; effective_threshold = threshold * context.preferences.system.pixel_size
        scene = context.scene

        # --->>> Use keys from the WM data <<<---
        obj_names = list(draw_data.keys())
        #print(f"DEBUG: Object names under cursor (from WM data): {obj_names}") # DEBUG

        for obj_name in obj_names:
            obj = scene.objects.get(obj_name)
            if not obj or not obj.visible_get(view_layer=context.view_layer) or not utils.is_sdf_source(obj): continue
            parent_bounds = utils.find_parent_bounds(obj)
            if not parent_bounds or not utils.get_bounds_setting(parent_bounds, "sdf_show_source_empties"): continue

            # --->>> Get lines from the WM data <<<---
            lines = draw_data.get(obj_name, [])
            if not lines: continue

            for p1_world, p2_world in lines:
                p1_screen = view3d_utils.location_3d_to_region_2d(region, region_data, p1_world)
                p2_screen = view3d_utils.location_3d_to_region_2d(region, region_data, p2_world)
                if p1_screen and p2_screen:
                    dist = utils.dist_point_to_segment_2d((mx, my), p1_screen, p2_screen)
                    if dist < effective_threshold and dist*dist < min_dist_sq:
                        min_dist_sq = dist*dist; closest_obj_name = obj_name
        return closest_obj_name

    def perform_selection(self, context, event, target_obj):
        extend = event.shift
        is_currently_active = context.view_layer.objects.active == target_obj
        is_currently_selected = target_obj.select_get()
        
        if extend:
            # Shift: Toggle selection, adjust active object
            if is_currently_active:
                # If active, deselect and find new active object
                if is_currently_selected:
                    # If selected, deselect it
                    target_obj.select_set(False)
                else:
                    # If not selected, deselect it
                    # and make it active
                    context.view_layer.objects.active = target_obj
                    target_obj.select_set(True)
            elif is_currently_selected:
                # If selected but not active, make it active
                context.view_layer.objects.active = target_obj
            else:
                # If not selected, select and make active
                target_obj.select_set(True)
                context.view_layer.objects.active = target_obj
            
        else:
            # No modifiers: Make target active and selected, handle active object deselection
            if is_currently_selected and is_currently_active and len(context.selected_objects) == 1:
                # If the object is active, selected, and the only selected object, deselect it
                target_obj.select_set(False)
                utils.find_and_set_new_active(context, target_obj)  # Try to set another object as active
            else:
                # Otherwise, deselect all, select target, and make it active
                bpy.ops.object.select_all(action='DESELECT')
                target_obj.select_set(True)
                context.view_layer.objects.active = target_obj

    def invoke(self, context, event):
        global _selection_handler_running
        if _selection_handler_running: return {'CANCELLED'}
        if context.window is None: return {'CANCELLED'}
        self.selecting_mouse_button = utils.get_blender_select_mouse() # Use utils version
        self.reset_drag_state()
        try: inputs_prefs=getattr(context.preferences, 'inputs', None); drag_thresh=getattr(inputs_prefs, 'drag_threshold', 3) if inputs_prefs else 3; self.drag_threshold_squared = drag_thresh * drag_thresh
        except AttributeError: self.drag_threshold_squared = 9
        if self.drag_threshold_squared < 4: self.drag_threshold_squared = 9
        try: self._timer = context.window_manager.event_timer_add(0.5, window=context.window); context.window_manager.modal_handler_add(self); _selection_handler_running = True; return {'RUNNING_MODAL'}
        except Exception as e: print(f"ERROR adding timer/modal handler: {e}"); _selection_handler_running = False; return {'CANCELLED'}

    def cancel_modal(self, context):
        global _selection_handler_running
        self.reset_drag_state();
        wm = getattr(context, 'window_manager', None);
        if self._timer and wm:
             try: wm.event_timer_remove(self._timer)
             except Exception: pass
             self._timer = None
        if _selection_handler_running: _selection_handler_running = False
        tag_redraw_all_view3d(); return {'CANCELLED'}

# --- END OF CLASS ---


# --- Function to Start Modal Handler (called by register) ---
def start_select_handler_via_timer(max_attempts=5, interval=0.2):
    """ Uses a timer with retries to robustly start the modal handler. """
    global _selection_handler_running # Access global flag
    current_attempt = 0
    # (Implementation from previous answer - NO CHANGES NEEDED HERE)
    # ... (defines attempt_invoke, registers timer) ...
    def attempt_invoke():
        nonlocal current_attempt
        current_attempt += 1
        context = bpy.context
        if not context or not context.window_manager or not context.window:
            if current_attempt < max_attempts: return interval
            else: print("FF Start Handler Failed: Invalid Context"); return None
        op_type = getattr(bpy.types, "VIEW3D_OT_fieldforge_select_handler", None)
        op_exists = hasattr(bpy.ops.view3d, 'fieldforge_select_handler')
        if not op_type or not op_exists:
            if current_attempt < max_attempts: return interval
            else: print("FF Start Handler Failed: Operator not found"); return None
        if _selection_handler_running: return None # Already running
        try: bpy.ops.view3d.fieldforge_select_handler('INVOKE_DEFAULT')
        except Exception as e: print(f"ERROR invoking handler: {e}")
        return None # Stop timer
    if not _selection_handler_running: bpy.app.timers.register(attempt_invoke, first_interval=interval)


# --- List of Operators to Register ---
classes_to_register = (
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
    OBJECT_OT_fieldforge_toggle_array_axis,
    OBJECT_OT_fieldforge_set_main_array_mode,
    OBJECT_OT_sdf_manual_update,
    VIEW3D_OT_fieldforge_select_handler,
)