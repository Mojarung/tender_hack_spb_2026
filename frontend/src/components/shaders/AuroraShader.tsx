"use client";

/**
 * Aurora-style WebGL background — flowing curtains of iridescent light.
 *
 * Heavier than MeshGradient but ~2 KB of shader code; still 60 fps on
 * integrated GPUs (DPR clamped at 1.25, full-screen triangle only).
 *
 * Use as an absolutely-positioned background. Mouse-reactive: cursor
 * gently warps the curtains. Honours `prefers-reduced-motion`.
 */

import { useEffect, useRef } from "react";

const VERT = /* glsl */ `
attribute vec2 position;
varying vec2 vUv;
void main() {
  vUv = position * 0.5 + 0.5;
  gl_Position = vec4(position, 0.0, 1.0);
}
`;

const FRAG = /* glsl */ `
precision highp float;

varying vec2 vUv;
uniform float uTime;
uniform vec2  uRes;
uniform vec2  uMouse;
uniform float uIntensity;
uniform vec3  uColor1;
uniform vec3  uColor2;
uniform vec3  uColor3;
uniform vec3  uBg;

// 2D simplex noise (Ashima, public domain) ────────────────────────────
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec2 mod289(vec2 x){return x-floor(x*(1.0/289.0))*289.0;}
vec3 permute(vec3 x){return mod289(((x*34.0)+1.0)*x);}
float snoise(vec2 v){
  const vec4 C = vec4(0.211324865405187, 0.366025403784439,
                      -0.577350269189626, 0.024390243902439);
  vec2 i  = floor(v + dot(v, C.yy));
  vec2 x0 = v - i + dot(i, C.xx);
  vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
  vec4 x12 = x0.xyxy + C.xxzz; x12.xy -= i1;
  i = mod289(i);
  vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0))
                          + i.x + vec3(0.0, i1.x, 1.0));
  vec3 m = max(0.5 - vec3(dot(x0,x0), dot(x12.xy,x12.xy), dot(x12.zw,x12.zw)), 0.0);
  m = m*m; m = m*m;
  vec3 x = 2.0 * fract(p * C.www) - 1.0;
  vec3 h = abs(x) - 0.5;
  vec3 ox = floor(x + 0.5);
  vec3 a0 = x - ox;
  m *= 1.79284291400159 - 0.85373472095314 * (a0*a0 + h*h);
  vec3 g; g.x = a0.x * x0.x + h.x * x0.y;
  g.yz = a0.yz * x12.xz + h.yz * x12.yw;
  return 130.0 * dot(m, g);
}

// fbm — fractal Brownian motion ───────────────────────────────────────
float fbm(vec2 p) {
  float v = 0.0; float a = 0.5;
  for (int i = 0; i < 5; i++) {
    v += a * snoise(p);
    p *= 2.05;
    a *= 0.5;
  }
  return v;
}

// One curtain of aurora at vertical position y.
float curtain(vec2 uv, float y, float t, float scale) {
  // Bend the curtain by fbm of x → wavy ribbon
  float wob = fbm(vec2(uv.x * 1.5 + t * 0.20, t * 0.30)) * 0.30;
  float band = 1.0 - smoothstep(0.0, 0.18 / scale, abs(uv.y - y - wob));
  // Vertical streaks within the band
  float streak = fbm(vec2(uv.x * 4.0, t * 0.6 + y * 7.0)) * 0.5 + 0.5;
  return band * streak;
}

void main() {
  // Aspect-correct uv anchored bottom-left = (0, 0)
  vec2 uv = vUv;
  uv.x *= uRes.x / uRes.y;

  // Mouse-warp: gentle pull
  vec2 m = uMouse * vec2(uRes.x / uRes.y, 1.0);
  uv += 0.06 * (m - uv) * smoothstep(0.5, 0.0, distance(uv, m));

  float t = uTime;

  // Three overlapping curtains at different heights / speeds / colours.
  float c1 = curtain(uv, 0.62 + 0.04 * sin(t * 0.20), t * 1.00, 1.0);
  float c2 = curtain(uv, 0.48 + 0.06 * sin(t * 0.15 + 1.7), t * 0.70 + 3.0, 1.3);
  float c3 = curtain(uv, 0.34 + 0.05 * sin(t * 0.25 + 4.1), t * 1.20 - 2.0, 0.85);

  vec3 col = uBg;
  col = mix(col, uColor1, clamp(c1, 0.0, 1.0) * 0.70);
  col = mix(col, uColor2, clamp(c2, 0.0, 1.0) * 0.65);
  col = mix(col, uColor3, clamp(c3, 0.0, 1.0) * 0.55);

  // Soft "stars" — tiny bright noise dots in the upper half.
  float stars = pow(fbm(uv * 18.0 + t * 0.04), 8.0) * smoothstep(0.4, 1.0, uv.y);
  col += vec3(stars * 0.5);

  // Edge-fade so the section reads as a self-contained block.
  float fade = smoothstep(0.0, 0.05, uv.y) *
               smoothstep(0.0, 0.05, 1.0 - uv.y) *
               smoothstep(0.0, 0.04, uv.x) *
               smoothstep(0.0, 0.04, (uRes.x / uRes.y) - uv.x);
  col = mix(uBg, col, fade);

  col *= uIntensity;

  // Subtle film grain
  float g = (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) * 0.02;
  col += g;

  gl_FragColor = vec4(col, 1.0);
}
`;

interface Props {
  bg?: [number, number, number];
  color1?: [number, number, number];
  color2?: [number, number, number];
  color3?: [number, number, number];
  /** Multiplier on output brightness. 0.8 = subtle, 1.2 = punchy. */
  intensity?: number;
  /** Time multiplier (0.5 calm — 1.5 lively). */
  speed?: number;
  className?: string;
}

export function AuroraShader({
  bg     = [0.04, 0.03, 0.10],
  color1 = [0.31, 0.27, 0.92],   // indigo
  color2 = [0.93, 0.30, 0.69],   // pink
  color3 = [0.20, 0.85, 0.69],   // teal
  intensity = 1.0,
  speed = 1.0,
  className,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let stopped = false;
    let raf = 0;

    (async () => {
      const { Renderer, Program, Mesh, Triangle, Vec2, Vec3 } = await import("ogl");

      const dpr = Math.min(window.devicePixelRatio || 1, 1.25);
      const renderer = new Renderer({
        dpr, alpha: false, antialias: false, powerPreference: "low-power",
      });
      const gl = renderer.gl;
      gl.canvas.style.cssText =
        "position:absolute;inset:0;width:100%;height:100%;display:block;";
      host.appendChild(gl.canvas);

      const reduceMotion =
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      const program = new Program(gl, {
        vertex: VERT,
        fragment: FRAG,
        uniforms: {
          uTime:      { value: 0 },
          uRes:       { value: new Vec2(1, 1) },
          uMouse:     { value: new Vec2(0.5, 0.5) },
          uIntensity: { value: intensity },
          uBg:        { value: new Vec3(...bg) },
          uColor1:    { value: new Vec3(...color1) },
          uColor2:    { value: new Vec3(...color2) },
          uColor3:    { value: new Vec3(...color3) },
        },
      });
      const mesh = new Mesh(gl, { geometry: new Triangle(gl), program });

      function resize() {
        const r = host!.getBoundingClientRect();
        renderer.setSize(r.width, r.height);
        program.uniforms.uRes.value.set(r.width, r.height);
      }
      resize();
      const ro = new ResizeObserver(resize);
      ro.observe(host);

      const target = new Vec2(0.5, 0.5);
      function onMouse(e: PointerEvent) {
        const r = host!.getBoundingClientRect();
        target.x = (e.clientX - r.left) / r.width;
        target.y = 1 - (e.clientY - r.top) / r.height;
      }
      window.addEventListener("pointermove", onMouse, { passive: true });

      const start = performance.now();
      let rendered = false;
      function frame(now: number) {
        if (stopped) return;
        if (document.hidden) { raf = requestAnimationFrame(frame); return; }
        if (reduceMotion && rendered) { raf = requestAnimationFrame(frame); return; }
        program.uniforms.uTime.value = ((now - start) / 1000) * speed;
        program.uniforms.uMouse.value.x +=
          (target.x - program.uniforms.uMouse.value.x) * 0.05;
        program.uniforms.uMouse.value.y +=
          (target.y - program.uniforms.uMouse.value.y) * 0.05;
        renderer.render({ scene: mesh });
        rendered = true;
        raf = requestAnimationFrame(frame);
      }
      raf = requestAnimationFrame(frame);

      (host as HTMLDivElement & { __cleanup?: () => void }).__cleanup = () => {
        stopped = true;
        cancelAnimationFrame(raf);
        ro.disconnect();
        window.removeEventListener("pointermove", onMouse);
        try { gl.getExtension("WEBGL_lose_context")?.loseContext(); } catch { /* noop */ }
        gl.canvas.remove();
      };
    })().catch((err) => {
      console.warn("[AuroraShader]", err);
    });

    return () => {
      const h = host as HTMLDivElement & { __cleanup?: () => void };
      h.__cleanup?.();
    };
  }, [bg, color1, color2, color3, intensity, speed]);

  return (
    <div
      ref={hostRef}
      aria-hidden="true"
      className={className}
      style={{
        position: "absolute",
        inset: 0,
        overflow: "hidden",
        pointerEvents: "none",
      }}
    />
  );
}
