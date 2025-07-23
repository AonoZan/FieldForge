import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector, Matrix
import numpy as np

# Debug gradient colors (RGB, range 0.0 to 1.0)
DEBUG_COLOR_START = (1.0, 0.0, 0.0)  # Red (far from shapes)
DEBUG_COLOR_END = (0.0, 0.0, 1.0)    # Blue (near shapes)

# Global variables
handle = None
shader_object = None        # For GPUShader
shape_texture = None        # For GPUTexture
shape_texture_buffer = None # For Buffer

# Maximum number of shapes our texture can hold
MAX_SHAPES = 20 # Keep it reasonable for testing
# Texture layout: 1 texel for type/params, 4 texels for matrix columns
SHAPE_TEXTURE_WIDTH = 1 + 4 # 5 texels wide per shape
SHAPE_TEXTURE_HEIGHT = MAX_SHAPES # Each shape gets a row

# Create empties for SDF shapes
def create_sdf_empties():
    # Define shapes with type, parameters, and initial location
    # Parameters:
    # Sphere: [radius]
    # Box: [size.x, size.y, size.z] (half-extents)
    # Torus: [major_radius, minor_radius]
    # Cylinder: [radius, half_height]
    shapes_to_create = [
        {"name": "SDF_Shape_Sphere1", "type": "sphere", "params": [0.7], "location": (0.0, 0.0, 0.0)},
        {"name": "SDF_Shape_Sphere2", "type": "sphere", "params": [0.5], "location": (-2.0, 1.0, 0.0)},
        {"name": "SDF_Shape_Box", "type": "box", "params": [0.6, 0.8, 0.4], "location": (2.0, -1.0, 0.0)},
        {"name": "SDF_Shape_Torus", "type": "torus", "params": [0.8, 0.25], "location": (0.0, 2.0, 0.0)},
        {"name": "SDF_Shape_Cylinder", "type": "cylinder", "params": [0.4, 1.0], "location": (0.0, -2.0, 0.0)},
        {"name": "SDF_Shape_SmallBox", "type": "box", "params": [0.2, 0.2, 0.2], "location": (1.0, 1.0, 1.0)},
    ]

    for shape_info in shapes_to_create:
        name = shape_info["name"]
        if name not in bpy.data.objects:
            bpy.ops.object.empty_add(type='PLAIN_AXES', location=shape_info["location"])
            empty = bpy.context.active_object
            empty.name = name
            empty.empty_display_size = 0.3
            # Store shape type and parameters as custom properties
            empty["sdf_type"] = shape_info["type"]
            empty["sdf_params"] = shape_info["params"] # Store as a list
            print(f"Created empty: {name} at {shape_info['location']} with type {shape_info['type']}")
        else:
            obj = bpy.data.objects[name]
            obj["sdf_type"] = shape_info["type"] # Ensure custom props are set if object exists
            obj["sdf_params"] = shape_info["params"]
            print(f"Empty {name} already exists, updated custom props.")

# Generate vertices for a full-screen quad
def generate_quad_vertices():
    return [
        (-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0),
        (-1.0, 1.0),  (1.0, -1.0), (1.0, 1.0),
    ]

# Shader code
vertex_shader = '''
in vec2 position;
out vec2 uv_frag; // Renamed to avoid conflict with GLSL built-in 'uv' if it exists
void main() {
    uv_frag = position * 0.5 + 0.5; // Map [-1, 1] to [0, 1]
    gl_Position = vec4(position, 0.0, 1.0); // NDC space, full-screen quad
}
'''

fragment_shader = '''

uniform vec3 cameraPos_world; // Camera position in world space
uniform float maxDist;
uniform bool isPerspective;
uniform mat4 viewProjectionMatrix; // World to Clip
uniform mat4 invViewProjectionMatrix; // Clip to World (for perspective ray)
uniform mat4 invViewMatrix;         // View to World (for ortho ray)

uniform vec2 viewportSize;

uniform vec3 debugColorStart;
uniform vec3 debugColorEnd;

uniform sampler2D shapeDataTexture; // Texture for shape data
uniform int numActiveShapes;

in vec2 uv_frag; // Received from vertex shader
out vec4 FragColor;

// --- SDF Primitives ---
// (p is point in world space, world_to_object_mat is obj.matrix_world.inverted())
// For texture approach, world_mat is obj.matrix_world from texture
// SDF functions now take world_mat and calculate inverse internally

mat4 get_matrix_from_texture(int shape_idx, int base_texel_x) {
    // Each matrix column is one RGBA texel
    vec4 c0 = texelFetch(shapeDataTexture, ivec2(base_texel_x + 0, shape_idx), 0);
    vec4 c1 = texelFetch(shapeDataTexture, ivec2(base_texel_x + 1, shape_idx), 0);
    vec4 c2 = texelFetch(shapeDataTexture, ivec2(base_texel_x + 2, shape_idx), 0);
    vec4 c3 = texelFetch(shapeDataTexture, ivec2(base_texel_x + 3, shape_idx), 0);
    return mat4(c0, c1, c2, c3); // GLSL mat4 constructor takes columns
}

float sdf_sphere(vec3 p_world, mat4 shape_world_matrix, float r) {
    mat4 inv_shape_matrix = inverse(shape_world_matrix);
    vec3 p_local = (inv_shape_matrix * vec4(p_world, 1.0)).xyz;
    // Scale handling (approximate for non-uniform scale by scaling distance)
    vec3 scale_vec = vec3(length(shape_world_matrix[0].xyz),
                          length(shape_world_matrix[1].xyz),
                          length(shape_world_matrix[2].xyz));
    float min_scale = max(0.0001, min(scale_vec.x, min(scale_vec.y, scale_vec.z)));
    // More robust for non-uniform: scale p_local before distance calculation
    p_local /= min_scale;
    return (length(p_local) - r) * min_scale;
}

float sdf_box(vec3 p_world, mat4 shape_world_matrix, vec3 b_half_extents) {
    mat4 inv_shape_matrix = inverse(shape_world_matrix);
    vec3 p_local = (inv_shape_matrix * vec4(p_world, 1.0)).xyz;
    vec3 scale_vec = vec3(length(shape_world_matrix[0].xyz),
                          length(shape_world_matrix[1].xyz),
                          length(shape_world_matrix[2].xyz));
    float min_scale = max(0.0001, min(scale_vec.x, min(scale_vec.y, scale_vec.z)));
    p_local /= min_scale;
    vec3 q = abs(p_local) - b_half_extents;
    float d_local = length(max(q, vec3(0.0))) + min(max(q.x, max(q.y, q.z)), 0.0);
    return d_local * min_scale;
}

float sdf_torus(vec3 p_world, mat4 shape_world_matrix, vec2 t_radii) { // t_radii.x = major, t_radii.y = minor
    mat4 inv_shape_matrix = inverse(shape_world_matrix);
    vec3 p_local = (inv_shape_matrix * vec4(p_world, 1.0)).xyz;
    vec3 scale_vec = vec3(length(shape_world_matrix[0].xyz),
                          length(shape_world_matrix[1].xyz),
                          length(shape_world_matrix[2].xyz));
    float min_scale = max(0.0001, min(scale_vec.x, min(scale_vec.y, scale_vec.z)));
    p_local /= min_scale;
    vec2 q = vec2(length(p_local.xz) - t_radii.x, p_local.y);
    float d_local = length(q) - t_radii.y;
    return d_local * min_scale;
}

float sdf_cylinder(vec3 p_world, mat4 shape_world_matrix, vec2 rh) { // rh.x = radius, rh.y = half_height
    mat4 inv_shape_matrix = inverse(shape_world_matrix);
    vec3 p_local = (inv_shape_matrix * vec4(p_world, 1.0)).xyz;
    vec3 scale_vec = vec3(length(shape_world_matrix[0].xyz),
                          length(shape_world_matrix[1].xyz),
                          length(shape_world_matrix[2].xyz));
    float min_scale = max(0.0001, min(scale_vec.x, min(scale_vec.y, scale_vec.z)));
    p_local /= min_scale;
    vec2 d_abs = abs(vec2(length(p_local.xz), p_local.y)) - rh;
    float d_local = min(max(d_abs.x, d_abs.y), 0.0) + length(max(d_abs, vec2(0.0)));
    return d_local * min_scale;
}

float smooth_min(float a, float b, float k) {
    float h = max(k - abs(a - b), 0.0) / k;
    return min(a, b) - h * h * k * 0.25;
}

// --- Scene SDF using texture data ---
float sdf_scene(vec3 p_world) {
    float d_final = maxDist;
    float k_smooth = 0.3; // Smoothness factor for combining shapes

    for (int i = 0; i < numActiveShapes; ++i) {
        // Texel 0: type_id (x), param1 (y), param2 (z), param3 (w)
        vec4 params_type = texelFetch(shapeDataTexture, ivec2(0, i), 0);
        float shape_type_id = params_type.x;

        // Texels 1-4: matrix columns
        mat4 shape_world_mat = get_matrix_from_texture(i, 1);

        float d_shape = maxDist;
        if (shape_type_id < 0.5) { // Sphere
            d_shape = sdf_sphere(p_world, shape_world_mat, params_type.y); // radius in y
        } else if (shape_type_id < 1.5) { // Box
            d_shape = sdf_box(p_world, shape_world_mat, params_type.yzw); // half-extents in yzw
        } else if (shape_type_id < 2.5) { // Torus
            d_shape = sdf_torus(p_world, shape_world_mat, params_type.yz); // major_r, minor_r in yz
        } else if (shape_type_id < 3.5) { // Cylinder
            d_shape = sdf_cylinder(p_world, shape_world_mat, params_type.yz); // radius, half_height in yz
        }

        d_final = smooth_min(d_final, d_shape, k_smooth);
    }
    return d_final;
}

// --- Normal and Shading ---
vec3 compute_normal(vec3 p_world) {
    float eps = 0.001; // Adjust epsilon based on scene scale and precision
    // Tetrahedral sampling for more robust normals
    vec2 h = vec2(eps, 0.0);
    return normalize( vec3( sdf_scene(p_world + h.xyy) - sdf_scene(p_world - h.xyy),
                           sdf_scene(p_world + h.yxy) - sdf_scene(p_world - h.yxy),
                           sdf_scene(p_world + h.yyx) - sdf_scene(p_world - h.yyx) ) );
    /* // Simpler finite differences (can be less stable)
    float d = sdf_scene(p_world);
    return normalize(vec3(
        sdf_scene(p_world + vec3(eps, 0.0, 0.0)) - d,
        sdf_scene(p_world + vec3(0.0, eps, 0.0)) - d,
        sdf_scene(p_world + vec3(0.0, 0.0, eps)) - d
    )); */
}

vec3 matcap_color(vec3 view_normal) { // Normal should be in view space for matcap
    // For simplicity here, using world normal and assuming a fixed matcap light
    vec3 world_normal = view_normal; // If normal passed is already world
    float light = dot(world_normal, normalize(vec3(0.5, 0.5, 1.0)));
    light = clamp(light, 0.0, 1.0);
    vec3 base_color = vec3(0.8, 0.85, 0.9);
    vec3 highlight = vec3(1.0, 1.0, 1.0);
    vec3 rim = vec3(0.3, 0.3, 0.35);
    vec3 color = mix(rim, base_color, smoothstep(0.0, 0.5, light));
    color = mix(color, highlight, smoothstep(0.7, 1.0, light));
    return color;
}

// --- Main Raymarching Logic ---
void main() {
    vec2 screen_uv = gl_FragCoord.xy / viewportSize; // UVs in [0, 1] range
    vec2 ndc = screen_uv * 2.0 - 1.0;             // UVs in [-1, 1] range (Normalized Device Coords)

    vec3 ray_origin_world;
    vec3 ray_direction_world;

    if (isPerspective) {
        ray_origin_world = cameraPos_world;
        // Unproject point on far plane to get world space direction
        vec4 far_clip = vec4(ndc.x, ndc.y, 1.0, 1.0); // Point on far plane in clip space
        vec4 far_world = invViewProjectionMatrix * far_clip;
        ray_direction_world = normalize(far_world.xyz / far_world.w - ray_origin_world);
    } else { // Orthographic
        // Unproject point on near plane to get world space origin for this pixel's ray
        vec4 near_clip = vec4(ndc.x, ndc.y, -1.0, 1.0); // Point on near plane in clip space
        vec4 near_world = invViewProjectionMatrix * near_clip; // invVP unprojects to world
        ray_origin_world = near_world.xyz / near_world.w;

        // Ray direction is constant for orthographic: camera's forward vector
        ray_direction_world = normalize((invViewMatrix * vec4(0.0, 0.0, -1.0, 0.0)).xyz);
    }

    float t = 0.0; // Distance along the ray
    float min_dist_to_surface = maxDist; // For debug coloring background
    const int MAX_RAY_STEPS = 256; // Max steps for raymarching
    const float HIT_EPSILON = 0.001; // Precision for surface hit

    for (int i = 0; i < MAX_RAY_STEPS; ++i) {
        vec3 current_pos_world = ray_origin_world + t * ray_direction_world;
        float dist_sdf = sdf_scene(current_pos_world);
        min_dist_to_surface = min(min_dist_to_surface, abs(dist_sdf));

        if (abs(dist_sdf) < HIT_EPSILON) {
            vec3 normal_world = compute_normal(current_pos_world);
            vec3 color_shaded = matcap_color(normal_world); // Using world normal for matcap simplicity

            // Depth calculation for Blender's Z-buffer
            vec4 pos_clip = viewProjectionMatrix * vec4(current_pos_world, 1.0);
            float depth_ndc = pos_clip.z / pos_clip.w; // Perspective divide
            gl_FragDepth = (depth_ndc * 0.5 + 0.5); // Map NDC depth [-1, 1] to [0, 1]

            FragColor = vec4(color_shaded, 1.0);
            return;
        }

        // Advance ray: ensure minimum step to avoid overshooting thin parts
        // Also, scale step by distance to go faster when far away
        t += max(dist_sdf * 0.8, HIT_EPSILON * 0.5); // Adjust step scaling (0.8) as needed

        if (t > maxDist) {
            break; // Ray missed or went too far
        }
    }

    // If ray missed (max steps or max distance reached)
    float debug_norm = clamp(min_dist_to_surface / (maxDist * 0.1), 0.0, 1.0); // Normalize for color mix
    debug_norm = pow(debug_norm, 0.5); // Gamma correct slightly
    vec3 color_bg = mix(debugColorEnd, debugColorStart, debug_norm); // Blue near, Red far
    FragColor = vec4(color_bg, 1.0);
    gl_FragDepth = 1.0; // Furthest depth
}
'''

# --- GPU Resource Management (using gpu.types.Buffer and gpu.types.GPUTexture) ---
def update_shape_texture_resources(objects_list):
    global shape_texture, shape_texture_buffer
    num_valid_shapes = 0

    # Data for one shape: type(1), params(3), mat_col0(4), mat_col1(4), mat_col2(4), mat_col3(4) = 16 floats
    # Texel layout:
    # Texel 0 (ivec2(0, shape_idx)): type_id, param1, param2, param3
    # Texel 1 (ivec2(1, shape_idx)): mat.col0.x, mat.col0.y, mat.col0.z, mat.col0.w
    # Texel 2 (ivec2(2, shape_idx)): mat.col1.x, ...
    # Texel 3 (ivec2(3, shape_idx)): mat.col2.x, ...
    # Texel 4 (ivec2(4, shape_idx)): mat.col3.x, ...

    # Total floats per shape = 4 (params/type) + 4*4 (matrix) = 20 floats
    # This means SHAPE_TEXTURE_WIDTH should be 5 (texels, each texel is RGBA float)
    # Each shape uses 1 row (shape_idx) and SHAPE_TEXTURE_WIDTH columns of texels.

    # NumPy array to hold all float data for the texture
    # Dimensions: MAX_SHAPES rows, SHAPE_TEXTURE_WIDTH columns, 4 floats (RGBA) per texel
    data_np = np.zeros(MAX_SHAPES * SHAPE_TEXTURE_WIDTH * 4, dtype=np.float32)

    type_map = {"sphere": 0.0, "box": 1.0, "torus": 2.0, "cylinder": 3.0}

    for i, obj in enumerate(objects_list):
        if i >= MAX_SHAPES: break # Don't exceed texture capacity

        sdf_type_str = obj.get("sdf_type", "sphere")
        sdf_params_list = obj.get("sdf_params", [1.0]) # Default to radius 1 sphere

        type_id = type_map.get(sdf_type_str, 0.0)

        # Pack type and params into the first texel (4 floats)
        # params_type_texel = [type_id] + sdf_params_list[:3] # Take up to 3 params
        # Pad if fewer than 3 params provided
        # params_type_texel_padded = params_type_texel + [0.0] * (4 - len(params_type_texel))

        # Simpler padding ensuring exactly 4 floats for the first texel
        current_params = [type_id]
        current_params.extend(sdf_params_list)
        # Ensure 4 floats for this texel data (type + 3 params)
        padded_params_for_texel = (current_params + [0.0, 0.0, 0.0])[:4]


        # Write params/type to texture data (texel column 0 for shape i)
        # Each shape is a row in the texture, data is laid out across texel columns
        # Offset for current shape's row in the 1D numpy array:
        # (shape_index * num_texels_per_row_width * num_floats_per_texel)
        # + (texel_column_index * num_floats_per_texel)

        base_idx_params = (i * SHAPE_TEXTURE_WIDTH * 4) + (0 * 4) # Texel X=0 for shape i
        data_np[base_idx_params : base_idx_params + 4] = padded_params_for_texel

        # Write matrix (transposed, so columns are read directly by GLSL)
        matrix = obj.matrix_world.transposed() # Transpose for GLSL mat4 constructor
        for col_idx in range(4):
            # Matrix col j goes into texel column (1+j) for shape i
            base_idx_matrix_col = (i * SHAPE_TEXTURE_WIDTH * 4) + ((1 + col_idx) * 4)
            data_np[base_idx_matrix_col : base_idx_matrix_col + 4] = matrix[col_idx]

        num_valid_shapes += 1

    data_list = data_np.tolist()
    buffer_len_elements = len(data_list) # Total number of floats
    buffer_changed_or_new = False

    try:
        if shape_texture_buffer is None or len(shape_texture_buffer) != buffer_len_elements:
            shape_texture_buffer = gpu.types.Buffer('FLOAT', buffer_len_elements)
            buffer_changed_or_new = True
        shape_texture_buffer[:] = data_list
        if not buffer_changed_or_new: buffer_changed_or_new = True # Content changed
    except Exception as e:
        print(f"FATAL: Error during gpu.types.Buffer creation or population: {e}")
        return None, 0

    if shape_texture_buffer is None: return None, 0

    tex_dims = (SHAPE_TEXTURE_WIDTH, SHAPE_TEXTURE_HEIGHT) # Width = texels per row, Height = num shapes
    if shape_texture is None or buffer_changed_or_new:
        try:
            shape_texture = gpu.types.GPUTexture(size=tex_dims, format='RGBA32F', data=shape_texture_buffer)
            # Attempt to set NEAREST filtering
            if hasattr(shape_texture, 'interpolation'): # Blender Image Texture property
                shape_texture.interpolation = 'Closest'
            elif hasattr(shape_texture, 'use_interpolation'): # Common boolean
                shape_texture.use_interpolation = False
            # print(f"DEBUG: GPUTexture created/recreated for {num_valid_shapes} shapes.")
        except Exception as e:
            print(f"FATAL: Error creating/recreating gpu.types.GPUTexture: {e}")
            return None, 0

    return shape_texture, num_valid_shapes


# --- Draw Callback ---
def draw():
    global shader_object # Uses gpu.types.GPUShader

    if shader_object is None: return

    context = bpy.context
    region = context.region
    region_3d = context.space_data.region_3d
    if not all([context, region, region_3d]) or region.width == 0 or region.height == 0: return

    # Collect SDF shape objects
    sdf_objects = []
    for obj in bpy.data.objects:
        if obj.name.startswith("SDF_Shape_") and obj.get("sdf_type") and obj.get("sdf_params"):
            sdf_objects.append(obj)

    # Sort objects by name to ensure consistent order in texture (optional but good for debugging)
    sdf_objects.sort(key=lambda o: o.name)

    # Update GPU resources with data from these objects
    texture, num_active_shapes = update_shape_texture_resources(sdf_objects[:MAX_SHAPES]) # Pass only up to MAX_SHAPES

    if texture is None or num_active_shapes == 0:
        # print("No shapes or texture to draw.") # Can be noisy
        return

    # --- Common Uniforms ---
    is_perspective = region_3d.is_perspective
    view_matrix = region_3d.view_matrix
    proj_matrix = region_3d.window_matrix # Use window_matrix for correct aspect in projection

    # For ray unprojection, we need world-to-clip and its inverse
    view_projection_matrix = proj_matrix @ view_matrix
    try:
        inv_view_projection_matrix = view_projection_matrix.inverted()
        inv_view_matrix = view_matrix.inverted() # For ortho ray direction
        camera_pos_world = inv_view_matrix.translation
    except ValueError: # Matrix not invertible
        print("Warning: View or Projection matrix not invertible.")
        return

    viewport_size = (float(region.width), float(region.height))

    # Ortho scale: Controls the "zoom" or view width/height for orthographic projection.
    # A common way is to base it on region_3d.view_distance (how far the view is "zoomed out")
    # Or, if using Blender's Orthographic Scale camera property, use that.
    # For manual ortho like this, view_distance is a good starting point.
    ortho_val = region_3d.view_distance * 0.5 # Default from your previous script
    if not is_perspective and ortho_val < 0.1: ortho_val = 0.1 # Ensure positive scale
    if is_perspective: ortho_val = 10.0 # Not directly used by perspective, but set a value

    max_dist_val = 100.0 # General max ray distance

    # --- Batch and Shader Binding ---
    vertices = generate_quad_vertices()
    batch = batch_for_shader(shader_object, 'TRIS', {"position": vertices})

    shader_object.bind()
    try:
        shader_object.uniform_float("cameraPos_world", camera_pos_world)
        shader_object.uniform_float("maxDist", max_dist_val)
        shader_object.uniform_bool("isPerspective", [is_perspective]) # Must be a list/tuple for bool
        shader_object.uniform_float("viewProjectionMatrix", view_projection_matrix)
        shader_object.uniform_float("invViewProjectionMatrix", inv_view_projection_matrix)
        shader_object.uniform_float("invViewMatrix", inv_view_matrix)
        shader_object.uniform_float("viewportSize", viewport_size)
        shader_object.uniform_float("debugColorStart", DEBUG_COLOR_START)
        shader_object.uniform_float("debugColorEnd", DEBUG_COLOR_END)
        shader_object.uniform_sampler("shapeDataTexture", texture) # Pass the GPUTexture object
        shader_object.uniform_int("numActiveShapes", [num_active_shapes]) # Must be a list/tuple for int

    except Exception as e:
        print(f"Uniform binding failed: {e}")
        return

    # --- Draw Call ---
    gpu.state.depth_test_set('LESS_EQUAL') # Use LESS_EQUAL for better integration
    gpu.state.depth_mask_set(True)
    gpu.state.blend_set('NONE') # Usually 'NONE' unless transparency needed
    batch.draw(shader_object)

    # Reset states
    gpu.state.depth_test_set('NONE')
    gpu.state.depth_mask_set(False)


# --- Handler Management ---
def scene_update_handler(scene):
    if bpy.context.window_manager: # Ensure window manager exists
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    for region_iter in area.regions:
                        if region_iter.type == 'WINDOW':
                            region_iter.tag_redraw()

def register():
    global handle, shader_object, shape_texture, shape_texture_buffer
    unregister() # Clean up previous state
    try:
        shader_object = gpu.types.GPUShader(vertex_shader, fragment_shader)
        print("Shader compiled successfully.")
    except Exception as e:
        print(f"FATAL: Shader compilation failed: {e}")
        shader_object = None; return # Don't proceed if shader fails

    # Reset GPU resources, they will be created in the first draw call
    shape_texture = None
    shape_texture_buffer = None

    # Add draw handler
    # POST_VIEW is generally better than POST_PIXEL for 3D overlays
    # as it draws after scene geometry but before UI, and respects depth better.
    handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_VIEW')
    print("Draw handler registered (POST_VIEW).")

    if scene_update_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(scene_update_handler)
        print("Depsgraph update handler registered.")

def unregister():
    global handle, shader_object, shape_texture, shape_texture_buffer
    if handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
        handle = None
    if 'scene_update_handler' in globals() and scene_update_handler in bpy.app.handlers.depsgraph_update_post:
        try: bpy.app.handlers.depsgraph_update_post.remove(scene_update_handler)
        except ValueError: pass # Already removed

    # Python's GC will handle the GPU objects when references are lost
    shader_object = None
    shape_texture = None
    shape_texture_buffer = None
    print("Handlers unregistered and GPU resource references cleared.")


# --- Script Execution / Example Usage ---
if __name__ == "__main__":
    unregister() # Clean up any previous state from script reloads
    create_sdf_empties()
    register()

# To use:
# 1. Run this script in Blender's Text Editor.
# 2. Empties named "SDF_Shape_..." will be created/updated.
# 3. The SDFs should render in the 3D Viewport.
# 4. To stop, re-run the script (it unregisters first) or run `unregister()` from Python console.
