#version 330
in vec2 texcoord;
uniform sampler2D tex;
uniform float corner_radius;
vec4 default_post_processing(vec4 color);

vec4 window_shader() {
    vec4 color = texelFetch(tex, ivec2(texcoord), 0);
    if (corner_radius > 0.0) {
        vec2 size = vec2(textureSize(tex, 0));
        vec2 q = abs(texcoord - size * 0.5) - (size * 0.5 - vec2(corner_radius));
        float distance = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - corner_radius;
        float edge = smoothstep(-2.5, -1.5, distance);
        color = mix(color, vec4(vec3(176.0 / 255.0), 1.0), edge);
    }
    return default_post_processing(color);
}
