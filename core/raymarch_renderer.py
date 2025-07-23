import bpy
import gpu
from gpu_extras.batch import batch_for_shader
import numpy as np
from mathutils import Matrix
from .. import utils
from .. import constants

# --- Constants ---
MAX_NODES = 128
NODE_TEXTURE_WIDTH = 6  # 1 (type/params) + 4 (matrix) + 1 (blend_factor)
NODE_TEXTURE_HEIGHT = MAX_NODES

# Node type IDs
TYPE_EMPTY = 0.0
TYPE_SPHERE = 1.0
TYPE_BOX = 2.0
TYPE_CYLINDER = 3.0
TYPE_TORUS = 4.0
TYPE_ROUND_BOX = 5.0
TYPE_PYRAMID = 6.0
TYPE_CONE = 7.0
TYPE_RING = 8.0
TYPE_CIRCLE = 9.0
TYPE_POLYGON = 10.0

# --- Shader Code ---

vertex_shader = '''
in vec2 position;
out vec2 uv_frag;
void main() {
    uv_frag = position * 0.5 + 0.5;
    gl_Position = vec4(position, 0.0, 1.0);
}
'''

fragment_shader = '''
uniform mat4 viewProjectionMatrix;
uniform mat4 invViewProjectionMatrix;
uniform mat4 invViewMatrix;
uniform vec3 cameraPos_world;
uniform vec2 viewportSize;
uniform bool isPerspective;

uniform sampler2D sdfTreeTexture;
uniform int numActiveNodes;
uniform float globalBlendModifier;

out vec4 final_color;

uniform float maxDist;
const int MAX_RAY_STEPS = 256;
const float HIT_EPSILON = 0.0001;

float sdf_sphere(vec3 p, float r) {
    return length(p) - r;
}

float sdf_box(vec3 p, vec3 b) {
    vec3 q = abs(p) - b;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}

float sdf_cylinder(vec3 p, float r, float h) {
    vec2 d = abs(vec2(length(p.xy), p.z)) - vec2(r, h);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));
}

float sdf_torus(vec3 p, float r1, float r2) {
    vec2 q = vec2(length(p.xy) - r1, p.z);
    return length(q) - r2;
}

float sdf_round_box(vec3 p, vec3 b, float r) {
    vec3 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(0.0, max(q.x, max(q.y, q.z))) - r;
}

float sdf_pyramid(vec3 p, float h) {
    // Reflect to positive XZ quadrant
    p.xz = abs(p.xz);

    // half base size
    float base_half_size = 0.5;

    // slope of the pyramid face
    float m = h / base_half_size;
    
    // Normalization factor for the slanted faces
    float norm_factor = 1.0 / sqrt(m*m + 1.0);

    // Project onto pyramid faces and normalize the distance
    float d = max(p.x * m + p.y - h, p.z * m + p.y - h) * norm_factor;

    // Clip below base
    d = max(d, -p.y);

    return d;
}

float sdf_cone(vec3 p, float r, float h_half) { // r = base radius, h_half = half-height
    float H_full = 2.0 * h_half; // Full height of the cone
    vec2 q = vec2(length(p.xy), p.z);

    // Defines a cone with tip at z=H_full and base at z=0, pointing up.
    // The conical surface equation is derived from the dot product of a point
    // on the cone's surface with the normal to the cone's slope.
    float d_conical = (q.x * H_full - (H_full - q.y) * r) / length(vec2(H_full, r));

    // Distance to the base plane at z=0. Negative inside.
    float d_base = -q.y;

    // Distance to the tip plane at z=H_full. Negative inside.
    float d_tip = q.y - H_full;

    // The final distance is the maximum of the distances to the three surfaces
    // (conical surface, base plane, tip plane), effectively taking their intersection.
    return max(d_conical, max(d_base, d_tip));
}

float sdf_ring(vec3 p, float r_outer, float r_inner) {
    // A 2D ring is a flat object on the XY plane.
    // We give it a small thickness to make it visible.
    // The thickness is proportional to the ring's radial width.
    float ring_radial_width = r_outer - r_inner;
    float height = ring_radial_width * 0.2; // Make it 20% of the width
    if (height <= 0.001) height = 0.001; // Ensure a minimum thickness

    // The SDF for a flat ring (annular cylinder) can be constructed
    // by taking a 2D box as a cross-section and revolving it.
    
    // The center of the cross-section rectangle is at a distance from the origin
    // corresponding to the average of the inner and outer radii.
    float ring_center_radius = (r_outer + r_inner) / 2.0;
    
    // The half-dimensions of the rectangular cross-section.
    vec2 rect_half_dims = vec2(ring_radial_width / 2.0, height / 2.0);

    // We transform the 3D point 'p' into a 2D coordinate system
    // where we can evaluate the 2D box SDF of the cross-section.
    // The new 'x' is the distance from the ring's centerline.
    // The new 'y' is the original z height.
    vec2 p_cross_section = vec2(length(p.xy) - ring_center_radius, p.z);

    // Now, we apply the 2D box SDF formula.
    vec2 d = abs(p_cross_section) - rect_half_dims;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}

float sdf_circle(vec3 p, float r) {
    // A 2D circle is a cylinder with a very small height.
    float height = 0.01; // Small height to make it visible
    vec2 d = abs(vec2(length(p.xy), p.z)) - vec2(r, height * 0.5);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));
}

float sdf_polygon(vec3 p, float r, float num_sides) {
    float height = 0.01;
    
    vec2 p_xy = p.xy;
    float a = atan(p_xy.x, p_xy.y) + 3.14159265359;
    
    // Add rotation offset for even-sided polygons
    if (mod(num_sides, 2.0) == 0.0) {
        a += 3.14159265359 / num_sides; // Half side angle
    }
    
    float b = 6.28318530718 / num_sides;
    float d_angle = mod(a, b) - b * 0.5;
    float d_radius = length(p_xy) - r * cos(b * 0.5) / cos(d_angle);

    vec2 d_prism = vec2(d_radius, abs(p.z) - height * 0.5);
    return min(max(d_prism.x, d_prism.y), 0.0) + length(max(d_prism, 0.0));
}

float op_smooth_union(float d1, float d2, float k) {
    if (k <= 0.0) {
        return min(d1, d2);
    }
    float m = 2.75 / (k * k);
    // Numerically stable implementation using log-sum-exp trick
    // -log(exp(-m*d1) + exp(-m*d2)) / m  <=>  min(d1, d2) - log(1 + exp(-m*abs(d1-d2)))/m
    float h = exp(-m * abs(d1 - d2));
    return min(d1, d2) - log(1.0 + h) / m;
}

float op_smooth_intersection(float d1, float d2, float k) {
    return -op_smooth_union(-d1, -d2, k);
}

float op_smooth_difference(float d1, float d2, float k) {
    return op_smooth_intersection(d1, -d2, k);
}

float get_scene_dist(vec3 p_world) {
    float scene_dist = maxDist;

    // Handle the first object separately to initialize scene_dist
    if (numActiveNodes > 0) {
        vec4 type_params = texelFetch(sdfTreeTexture, ivec2(0, 0), 0);
        float node_type = type_params.x;

        mat4 shape_world_matrix = mat4(
            texelFetch(sdfTreeTexture, ivec2(1, 0), 0),
            texelFetch(sdfTreeTexture, ivec2(2, 0), 0),
            texelFetch(sdfTreeTexture, ivec2(3, 0), 0),
            texelFetch(sdfTreeTexture, ivec2(4, 0), 0)
        );

        mat4 inv_shape_matrix = inverse(shape_world_matrix);
        vec3 p_local = (inv_shape_matrix * vec4(p_world, 1.0)).xyz;
        float dist_local = maxDist;

        if (node_type < 1.5) { // Sphere
            dist_local = sdf_sphere(p_local, type_params.y);
        } else if (node_type < 2.5) { // Box
            dist_local = sdf_box(p_local, type_params.yzw);
        } else if (node_type < 3.5) { // Cylinder
            dist_local = sdf_cylinder(p_local, type_params.y, type_params.z);
        } else if (node_type < 4.5) { // Torus
            dist_local = sdf_torus(p_local, type_params.y, type_params.z);
        } else if (node_type < 5.5) { // Round Box
            float round_radius = texelFetch(sdfTreeTexture, ivec2(5, 0), 0).z;
            dist_local = sdf_round_box(p_local, type_params.yzw, round_radius);
        } else if (node_type < 6.5) { // Pyramid
            vec3 rotated_p_local = vec3(p_local.x, p_local.z, p_local.y);
            dist_local = sdf_pyramid(rotated_p_local, type_params.y);
        } else if (node_type < 7.5) { // Cone
            dist_local = sdf_cone(p_local, type_params.y, type_params.z);
        } else if (node_type < 8.5) { // Ring
            dist_local = sdf_ring(p_local, type_params.y, type_params.z);
        } else if (node_type < 9.5) { // Circle
            dist_local = sdf_circle(p_local, type_params.y);
        } else if (node_type < 10.5) { // Polygon
            dist_local = sdf_polygon(p_local, type_params.y, type_params.z);
        }

        vec3 scale_vec = vec3(length(shape_world_matrix[0].xyz), length(shape_world_matrix[1].xyz), length(shape_world_matrix[2].xyz));
        float min_scale = max(0.0001, min(scale_vec.x, min(scale_vec.y, scale_vec.z)));
        scene_dist = dist_local * min_scale;
    }

    // Iterate through the rest of the objects, applying CSG operations
    for (int i = 1; i < numActiveNodes; ++i) {
        vec4 type_params = texelFetch(sdfTreeTexture, ivec2(0, i), 0);
        float node_type = type_params.x;

        mat4 shape_world_matrix = mat4(
            texelFetch(sdfTreeTexture, ivec2(1, i), 0),
            texelFetch(sdfTreeTexture, ivec2(2, i), 0),
            texelFetch(sdfTreeTexture, ivec2(3, i), 0),
            texelFetch(sdfTreeTexture, ivec2(4, i), 0)
        );

        mat4 inv_shape_matrix = inverse(shape_world_matrix);
        vec3 p_local = (inv_shape_matrix * vec4(p_world, 1.0)).xyz;

        float dist_local = maxDist;
        if (node_type < 1.5) { // Sphere
            dist_local = sdf_sphere(p_local, type_params.y);
        } else if (node_type < 2.5) { // Box
            dist_local = sdf_box(p_local, type_params.yzw);
        } else if (node_type < 3.5) { // Cylinder
            dist_local = sdf_cylinder(p_local, type_params.y, type_params.z);
        } else if (node_type < 4.5) { // Torus
            dist_local = sdf_torus(p_local, type_params.y, type_params.z);
        } else if (node_type < 5.5) { // Round Box
            float round_radius = texelFetch(sdfTreeTexture, ivec2(5, i), 0).z;
            dist_local = sdf_round_box(p_local, type_params.yzw, round_radius);
        } else if (node_type < 6.5) { // Pyramid
            vec3 rotated_p_local = vec3(p_local.x, p_local.z, p_local.y);
            dist_local = sdf_pyramid(rotated_p_local, type_params.y);
        } else if (node_type < 7.5) { // Cone
            dist_local = sdf_cone(p_local, type_params.y, type_params.z);
        } else if (node_type < 8.5) { // Ring
            dist_local = sdf_ring(p_local, type_params.y, type_params.z);
        } else if (node_type < 9.5) { // Circle
            dist_local = sdf_circle(p_local, type_params.y);
        } else if (node_type < 10.5) { // Polygon
            dist_local = sdf_polygon(p_local, type_params.y, type_params.z);
        }

        vec3 scale_vec = vec3(length(shape_world_matrix[0].xyz), length(shape_world_matrix[1].xyz), length(shape_world_matrix[2].xyz));
        float min_scale = max(0.0001, min(scale_vec.x, min(scale_vec.y, scale_vec.z)));
        float dist_world = dist_local * min_scale;

        float blend_k = texelFetch(sdfTreeTexture, ivec2(5, i), 0).x;
        float csg_op = texelFetch(sdfTreeTexture, ivec2(5, i), 0).y;

        if (csg_op < 0.5) { // Union
            scene_dist = op_smooth_union(scene_dist, dist_world, blend_k);
        } else if (csg_op < 1.5) { // Difference
            scene_dist = op_smooth_difference(scene_dist, dist_world, blend_k);
        } else { // Intersection
            scene_dist = op_smooth_intersection(scene_dist, dist_world, blend_k);
        }
    }
    return scene_dist;
}


vec3 compute_normal(vec3 p_world) {
    vec2 h = vec2(0.001, 0.0);
    return normalize(vec3(
        get_scene_dist(p_world + h.xyy) - get_scene_dist(p_world - h.xyy),
        get_scene_dist(p_world + h.yxy) - get_scene_dist(p_world - h.yxy),
        get_scene_dist(p_world + h.yyx) - get_scene_dist(p_world - h.yyx)
    ));
}

vec3 matcap_shading(vec3 normal) {
    float light = dot(normal, normalize(vec3(0.5, 0.5, 1.0)));
    light = clamp(light, 0.0, 1.0);
    vec3 base_color = vec3(0.8, 0.85, 0.9);
    vec3 highlight = vec3(1.0, 1.0, 1.0);
    vec3 rim = vec3(0.3, 0.3, 0.35);
    vec3 color = mix(rim, base_color, smoothstep(0.0, 0.5, light));
    color = mix(color, highlight, smoothstep(0.7, 1.0, light));
    return color;
}


void main() {
    vec2 ndc = (gl_FragCoord.xy / viewportSize) * 2.0 - 1.0;

    vec3 ray_origin_world;
    vec3 ray_direction_world;

    if (isPerspective) {
        ray_origin_world = cameraPos_world;
        vec4 far_world = invViewProjectionMatrix * vec4(ndc, 1.0, 1.0);
        ray_direction_world = normalize(far_world.xyz / far_world.w - ray_origin_world);
    } else {
        vec4 near_world = invViewProjectionMatrix * vec4(ndc, -1.0, 1.0);
        ray_origin_world = near_world.xyz / near_world.w;
        ray_direction_world = normalize((invViewMatrix * vec4(0.0, 0.0, -1.0, 0.0)).xyz);
    }

    float t = 0.0;
    const float HIT_EPSILON = 0.001; 

    for (int i = 0; i < MAX_RAY_STEPS; ++i) {
        vec3 p = ray_origin_world + t * ray_direction_world;
        float dist = get_scene_dist(p);

        if (abs(dist) < HIT_EPSILON) {
            vec3 normal = compute_normal(p);
            vec3 final_render_color = matcap_shading(normal);
            final_color = vec4(final_render_color, 1.0);

            vec4 pos_clip = viewProjectionMatrix * vec4(p, 1.0);
            gl_FragDepth = (pos_clip.z / pos_clip.w) * 0.5 + 0.5;

            return;
        }
        
        t += max(dist * 0.8, HIT_EPSILON * 0.5); 

        if (t > maxDist) {
            break; 
        }
    }

    discard;
}
'''

# --- GPU Resource Management ---
class RaymarchRenderer:
    def __init__(self):
        self.shader = None
        self.texture_buffer = None
        self.texture = None
        self.draw_handler = None
        self.is_active = False
        self.tree_data = []
        self.was_originally_visible = False

    def _compile_shader(self):
        try:
            self.shader = gpu.types.GPUShader(vertex_shader, fragment_shader)
        except Exception as e:
            print(f"FieldForge Raymarcher: Shader compilation failed: {e}")
            self.shader = None

    def _update_texture(self, sdf_tree_data):
        num_nodes = len(sdf_tree_data)
        if num_nodes == 0:
            self.texture = None
            return 0

        buffer_len = MAX_NODES * NODE_TEXTURE_WIDTH * 4
        data_np = np.zeros(buffer_len, dtype=np.float32)

        for i, node_data in enumerate(sdf_tree_data):
            if i >= MAX_NODES: break

            base_idx = (i * NODE_TEXTURE_WIDTH * 4)
            
            # Pack params (type, x, y, z) into first texel
            # For cylinder, params[1] is radius, params[2] is height
            params = (node_data.get('params', []) + [0.0, 0.0, 0.0, 0.0])[:4]
            data_np[base_idx : base_idx + 4] = params

            # Pack matrix into next 4 texels
            matrix_flat = np.array(node_data.get('matrix', Matrix.Identity(4).transposed())).flatten()
            data_np[base_idx + 4 : base_idx + 20] = matrix_flat

            # Pack blend factor and csg op into the 6th texel
            blend_factor = node_data.get('blend_factor', 0.2)
            csg_op = node_data.get('csg_op', 0.0)
            round_radius = node_data.get('round_radius', 0.0) # Default to 0.0 if not present
            data_np[base_idx + 20] = blend_factor
            data_np[base_idx + 21] = csg_op
            data_np[base_idx + 22] = round_radius

            

        data_list = data_np.tolist()
        if self.texture_buffer is None or len(self.texture_buffer) != buffer_len:
            self.texture_buffer = gpu.types.Buffer('FLOAT', buffer_len)

        self.texture_buffer[:] = data_list

        tex_dims = (NODE_TEXTURE_WIDTH, NODE_TEXTURE_HEIGHT)
        if self.texture is None:
             self.texture = gpu.types.GPUTexture(size=tex_dims, format='RGBA32F', data=self.texture_buffer)
        else:
             self.texture = gpu.types.GPUTexture(size=tex_dims, format='RGBA32F', data=self.texture_buffer)

        return num_nodes


    def _draw_callback(self):
        if not self.shader or not self.is_active:
            return

        from .. import utils
        from . import sdf_logic, state

        active_bounds = utils.find_parent_bounds(bpy.context.active_object)
        if not active_bounds:
            if utils.is_sdf_bounds(bpy.context.active_object):
                active_bounds = bpy.context.active_object
            else:
                return

        sdf_node_tree = sdf_logic.build_sdf_node_tree(bpy.context, active_bounds)
        if not sdf_node_tree:
            self.tree_data = []
        else:
            self.tree_data = state.get_sdf_tree_for_raymarching(bpy.context, sdf_node_tree)

        if not self.tree_data:
            return

        num_active_nodes = self._update_texture(self.tree_data)
        if not self.texture or num_active_nodes == 0:
            return

        context = bpy.context
        region = context.region
        region_3d = context.space_data.region_3d

        view_matrix = region_3d.view_matrix
        proj_matrix = region_3d.window_matrix
        view_projection_matrix = proj_matrix @ view_matrix
        try:
            inv_view_projection_matrix = view_projection_matrix.inverted()
            inv_view_matrix = view_matrix.inverted()
            camera_pos_world = inv_view_matrix.translation
        except ValueError:
            return # Matrix not invertible

        batch = batch_for_shader(self.shader, 'TRIS', {"position": [(-1, -1), (1, -1), (-1, 1), (-1, 1), (1, -1), (1, 1)]})

        self.shader.bind()
        self.shader.uniform_float("viewProjectionMatrix", view_projection_matrix)
        self.shader.uniform_float("invViewProjectionMatrix", inv_view_projection_matrix)
        self.shader.uniform_float("invViewMatrix", inv_view_matrix)
        self.shader.uniform_float("cameraPos_world", camera_pos_world)
        self.shader.uniform_float("viewportSize", (float(region.width), float(region.height)))
        self.shader.uniform_bool("isPerspective", [region_3d.is_perspective])
        self.shader.uniform_sampler("sdfTreeTexture", self.texture)
        self.shader.uniform_int("numActiveNodes", [num_active_nodes])
        self.shader.uniform_float("maxDist", 1000.0);
        

        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.blend_set('ALPHA')
        batch.draw(self.shader)
        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set('NONE')


    def start(self):
        if self.is_active:
            return
        if self.shader is None:
            self._compile_shader()
        if self.shader is None: # Still None after trying to compile
            return

        active_bounds = utils.find_parent_bounds(bpy.context.active_object)
        if not active_bounds:
            if utils.is_sdf_bounds(bpy.context.active_object):
                active_bounds = bpy.context.active_object
            else:
                return
        
        result_obj_name = active_bounds.get(constants.SDF_RESULT_OBJ_NAME_PROP, "")
        result_obj = bpy.context.scene.objects.get(result_obj_name)
        if result_obj:
            self.was_originally_visible = not result_obj.hide_viewport
            result_obj.hide_viewport = True

        self.draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback, (), 'WINDOW', 'POST_VIEW'
        )
        self.is_active = True
        print("FieldForge Raymarcher: Started")


    def stop(self):
        if not self.is_active:
            return
        if self.draw_handler:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handler, 'WINDOW')
            self.draw_handler = None
        
        active_bounds = utils.find_parent_bounds(bpy.context.active_object)
        if not active_bounds:
            if utils.is_sdf_bounds(bpy.context.active_object):
                active_bounds = bpy.context.active_object

        if active_bounds:
            result_obj_name = active_bounds.get(constants.SDF_RESULT_OBJ_NAME_PROP, "")
            result_obj = bpy.context.scene.objects.get(result_obj_name)
            if result_obj:
                result_obj.hide_viewport = not self.was_originally_visible

        self.is_active = False
        # Tag redraw to remove the overlay
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        print("FieldForge Raymarcher: Stopped")

    def cleanup(self):
        self.stop()
        self.shader = None
        self.texture = None
        self.texture_buffer = None


# --- Singleton Instance ---
_renderer_instance = None

def get_renderer():
    global _renderer_instance
    if _renderer_instance is None:
        _renderer_instance = RaymarchRenderer()
    return _renderer_instance
