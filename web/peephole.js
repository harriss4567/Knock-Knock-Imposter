// The view through the door's peephole: the avatar's video, drawn with WebGL through a fisheye lens,
// lit by a dim, flickering porch light from above. It hides the avatar's imperfections (and its
// tendency to smile) the way a real peephole at night would: shadow, distortion, grain.

const VERTEX = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() {
  v_uv = a_pos * 0.5 + 0.5;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

const FRAGMENT = `
precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_time;
uniform float u_light;      // porch light brightness, flickers in JS
uniform float u_aspect;     // video width / height
uniform float u_hasVideo;
uniform float u_flash;      // 0..1, the reveal
uniform float u_shadow;     // 0..1, how dark the lower face is

float rand(vec2 co) { return fract(sin(dot(co, vec2(12.9898, 78.233))) * 43758.5453); }

// Lens point (-1..1, y up) to video texture coordinates. The circle shows a square crop around the face.
vec2 toTex(vec2 p) {
  return vec2(0.5 + p.x * 0.5 * 0.92, 0.40 - p.y * 0.5 * 0.92 * u_aspect);
}

vec3 sampleSoft(vec2 uv) {
  // A little blur: the peephole lens is cheap and the night is dark.
  float d = 0.0025;
  vec3 c = texture2D(u_tex, uv).rgb * 0.4;
  c += texture2D(u_tex, uv + vec2(d, 0.0)).rgb * 0.15;
  c += texture2D(u_tex, uv - vec2(d, 0.0)).rgb * 0.15;
  c += texture2D(u_tex, uv + vec2(0.0, d)).rgb * 0.15;
  c += texture2D(u_tex, uv - vec2(0.0, d)).rgb * 0.15;
  return c;
}

void main() {
  vec2 p = v_uv * 2.0 - 1.0;
  float r = length(p);
  if (r > 1.0) { gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0); return; }

  // Fisheye: magnify the middle, squeeze the edges.
  vec2 lens = p * mix(0.62, 1.0, r * r);

  // Chromatic fringing towards the rim.
  float ca = 0.012 * r * r;
  vec3 col;
  col.r = sampleSoft(toTex(lens * (1.0 + ca))).r;
  col.g = sampleSoft(toTex(lens)).g;
  col.b = sampleSoft(toTex(lens * (1.0 - ca))).b;
  col *= u_hasVideo;

  // Sodium porch light: desaturate, then tint yellow-orange.
  float lum = dot(col, vec3(0.299, 0.587, 0.114));
  col = mix(vec3(lum), col, 0.25) * vec3(1.05, 0.82, 0.52);

  // Light from above: the lower face (mouth, chin) falls into shadow.
  float shade = 1.0 - u_shadow * (1.0 - smoothstep(-0.85, 0.05, p.y));
  col *= shade * u_light;

  // Crush blacks, vignette the lens, add grain.
  col = pow(max(col, 0.0), vec3(1.3));
  col *= 1.0 - 0.9 * smoothstep(0.5, 1.0, r);
  col += (rand(v_uv * 731.0 + u_time) - 0.5) * 0.09;
  col += vec3(0.02, 0.014, 0.008); // the darkness outside is never fully black
  col = mix(col, vec3(1.0, 0.93, 0.8), u_flash);

  gl_FragColor = vec4(col, 1.0);
}`;

class Peephole {
  constructor(canvas) {
    this.canvas = canvas;
    this.video = null;
    this.light = 1.0;
    this.dipUntil = 0;
    this.flashAt = -1e9;
    this.shadow = 0.6;
    const gl = canvas.getContext("webgl", { antialias: false });
    if (!gl) throw new Error("WebGL is not available");
    this.gl = gl;

    const program = gl.createProgram();
    for (const [type, src] of [[gl.VERTEX_SHADER, VERTEX], [gl.FRAGMENT_SHADER, FRAGMENT]]) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, src);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    gl.useProgram(program);
    this.u = {};
    for (const name of ["u_tex", "u_time", "u_light", "u_aspect", "u_hasVideo", "u_flash", "u_shadow"]) {
      this.u[name] = gl.getUniformLocation(program, name);
    }

    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const pos = gl.getAttribLocation(program, "a_pos");
    gl.enableVertexAttribArray(pos);
    gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);

    this.texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 255]));

    requestAnimationFrame((t) => this.frame(t));
  }

  setVideo(video) { this.video = video; }

  setShadow(strength) { this.shadow = strength; }

  // A burst of white light through the lens that fades out over a second and a half.
  flash() { this.flashAt = performance.now(); }

  // A tired bulb: a slow hum, and every few seconds a sharp dip.
  flicker(t) {
    if (t > this.dipUntil && Math.random() < 0.012) {
      this.dipUntil = t + 50 + Math.random() * 180;
      this.dipLevel = 0.35 + Math.random() * 0.4;
    }
    const hum = 0.93 + 0.05 * Math.sin(t / 90) + 0.02 * Math.sin(t / 23);
    return t < this.dipUntil ? this.dipLevel : hum;
  }

  frame(t) {
    const { gl, canvas, video } = this;
    const size = Math.round(canvas.clientWidth * Math.min(devicePixelRatio, 2));
    if (canvas.width !== size) { canvas.width = size; canvas.height = size; }
    gl.viewport(0, 0, size, size);

    const hasVideo = video && video.readyState >= 2 && video.videoWidth > 0;
    if (hasVideo) {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
    }
    gl.uniform1i(this.u.u_tex, 0);
    gl.uniform1f(this.u.u_time, (t / 1000) % 100);
    gl.uniform1f(this.u.u_light, this.flicker(t));
    gl.uniform1f(this.u.u_aspect, hasVideo ? video.videoWidth / video.videoHeight : 0.75);
    gl.uniform1f(this.u.u_hasVideo, hasVideo ? 1 : 0);
    gl.uniform1f(this.u.u_shadow, this.shadow);
    gl.uniform1f(this.u.u_flash, Math.max(0, 1 - (t - this.flashAt) / 1500) ** 2);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    requestAnimationFrame((t2) => this.frame(t2));
  }
}
