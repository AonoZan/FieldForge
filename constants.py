"""
Constants and default settings used throughout the FieldForge addon.
"""

# --- Custom Property Keys ---
# Used to identify specific object types or store associated data

# Marker for the main SDF Bounds controller Empty object
SDF_BOUNDS_MARKER = "is_sdf_bounds"

# Custom property on the Bounds object storing the name of the target result Mesh object
SDF_RESULT_OBJ_NAME_PROP = "sdf_result_object_name"

# Marker for SDF Source Empty objects (controllers for individual shapes)
SDF_PROPERTY_MARKER = "is_sdf_object"

# Marker for SDF Group Empty objects
SDF_GROUP_MARKER = "is_sdf_group"
SDF_CANVAS_MARKER = "is_sdf_canvas" 

SDF_LINK_TARGET_NAME_PROP = "sdf_link_target_name"
SDF_PROCESS_LINKED_CHILDREN_PROP = "sdf_process_linked_children"

# Axis constants on Python side for C++
AXIS_X_BIT = 1
AXIS_Y_BIT = 2
AXIS_Z_BIT = 4

_2D_SHAPE_TYPES = {"circle", "ring", "polygon", "text"}

# --- Caching and Comparison ---

# Tolerance for floating-point comparisons in caching state changes
CACHE_PRECISION = 1e-5

unit_cube_verts = [
    # Bottom face (-Z)
    (-0.5, -0.5, -0.5), (+0.5, -0.5, -0.5), (+0.5, +0.5, -0.5), (-0.5, +0.5, -0.5),
    # Top face (+Z)
    (-0.5, -0.5, +0.5), (+0.5, -0.5, +0.5), (+0.5, +0.5, +0.5), (-0.5, +0.5, +0.5),
]

# Indices to draw the 12 edges of the unit cube using LINES primitive
unit_cube_indices = [
    # Bottom face edges
    (0, 1), (1, 2), (2, 3), (3, 0),
    # Top face edges
    (4, 5), (5, 6), (6, 7), (7, 4),
    # Connecting vertical edges
    (0, 4), (1, 5), (2, 6), (3, 7)
]

# --- Default Settings ---
# These values are applied as custom properties to newly created Bounds objects.
# They are also used as fallbacks if a property is missing from an existing Bounds object.
DEFAULT_SETTINGS = {
    # --- Meshing Resolution ---
    "sdf_final_resolution": 30,     # Resolution for manual 'Update Final SDF' / potential future render export
    "sdf_viewport_resolution": 10,  # Resolution for automatic viewport previews (lower for performance)

    # --- Automatic Update Behavior ---
    "sdf_auto_update": True,        # Enable/disable automatic viewport updates on changes
    "sdf_realtime_update_delay": 0.3, # Inactivity time (seconds) before attempting auto viewport update
    "sdf_minimum_update_interval": 0.5, # Minimum time (seconds) between the *end* of one auto update and the *start* of the next (throttling)


    # --- Display and Object Management ---
    "sdf_show_source_empties": True, # Toggle visibility of source Empties AND custom draw outlines
    "sdf_create_result_object": True, # Allow auto-creation of the result mesh object if it's missing during an update

    "sdf_discard_mesh_on_save": True, # Don't save generated mesh by default
    "sdf_result_auto_smooth_angle": 44.9,
    "sdf_result_material_name": "",
    SDF_LINK_TARGET_NAME_PROP: "",
}

# Default values for Canvas Object properties ---
DEFAULT_CANVAS_SETTINGS = {
    "sdf_extrusion_depth": 0.1,
    "sdf_blend_factor": 0.0,
    "sdf_csg_operation": "UNION",
    "sdf_canvas_use_revolve": False,
    SDF_LINK_TARGET_NAME_PROP: "",
    SDF_PROCESS_LINKED_CHILDREN_PROP: False,
}

# Default values for Group Object properties ---
DEFAULT_GROUP_SETTINGS = {
    "sdf_blend_factor": 0.1,
    "sdf_csg_operation": "UNION",
    "sdf_group_symmetry_x": False,
    "sdf_group_symmetry_y": False,
    "sdf_group_symmetry_z": False,
    "sdf_group_taper_z_active": False,
    "sdf_group_taper_z_factor": 0.5,
    "sdf_group_taper_z_height": 1.0,
    "sdf_group_taper_z_base_scale": 1.0,
    "sdf_group_shear_x_by_y_active": False,
    "sdf_group_shear_x_by_y_offset": 0.5,
    "sdf_group_shear_x_by_y_base_offset": 0.0,
    "sdf_group_shear_x_by_y_height": 1.0,
    "sdf_group_attract_repel_mode": 'NONE',
    "sdf_group_attract_repel_radius": 0.5,
    "sdf_group_attract_repel_exaggerate": 1.0,
    "sdf_group_attract_repel_axis_x": True,
    "sdf_group_attract_repel_axis_y": True,
    "sdf_group_attract_repel_axis_z": True,
    "sdf_group_twirl_active": False,
    "sdf_group_twirl_axis": 'Z',
    "sdf_group_twirl_amount": 1.5708,
    "sdf_group_twirl_radius": 1.0,
    "sdf_main_array_mode": 'NONE',
    "sdf_array_active_x": False,
    "sdf_array_active_y": False,
    "sdf_array_active_z": False,
    "sdf_array_count_x": 2,
    "sdf_array_count_y": 2,
    "sdf_array_count_z": 2,
    "sdf_radial_count": 6,
    "sdf_radial_center": (0.0, 0.0),
    SDF_LINK_TARGET_NAME_PROP: "",
    SDF_PROCESS_LINKED_CHILDREN_PROP: False,
}

# --- Optional: Default values for Source Object properties ---
# While these are set in the Add operators, having them here could be useful for reference
# or if you needed to reset properties on an existing source.
DEFAULT_SOURCE_SETTINGS = {
    "sdf_blend_factor": 0.0,
    "sdf_csg_operation": "UNION",
    "sdf_use_clearance": False,
    "sdf_clearance_offset": 0.05,
    "sdf_clearance_keep_original": True,
    "sdf_use_morph": False,
    "sdf_morph_factor": 0.5,
    "sdf_use_loft": False,
    "sdf_use_shell": False,
    "sdf_shell_offset": 0.1,
    # Shape Specific Defaults (used in operators)
    "sdf_torus_major_radius": 0.35,
    "sdf_torus_minor_radius": 0.15,
    "sdf_round_radius": 0.1,
    "sdf_extrusion_depth": 0.1,
    "sdf_inner_radius": 0.25, # Ring inner radius (unit space)
    "sdf_sides": 6,           # Polygon sides
    "sdf_text_string": "Text",
    SDF_LINK_TARGET_NAME_PROP: "",
    SDF_PROCESS_LINKED_CHILDREN_PROP: False,
}