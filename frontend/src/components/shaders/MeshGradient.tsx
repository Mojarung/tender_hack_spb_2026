"use client";

/**
 * Animated mesh-gradient WebGL background.
 *
 * Why OGL: ~30KB minified, single-purpose WebGL helper. No three.js bulk.
 * Why this shader: 3D-simplex noise (Ashima) sampled at slow time +
 * mouse-warped UV → soft moving blobs in our indigo accent palette.
 *
 * Performance:
 *   - requestAnimationFrame, paused via Page Visibility API.
 *   - ResizeObserver-driven canvas sizing capped at devicePixelRatio=1.5.
 *   - prefers-reduced-motion: render once, freeze.
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

// Ashima 3D simplex noise — public domain.
const FRAG = /* glsl */ `
precision highp float;

varying vec2 vUv;
uniform float uTime;
uniform vec2 uRes;
uniform vec2 uMouse;
uniform vec3 uColorA;
uniform vec3 uColorB;
uniform vec3 uColorC;
uniform vec3 uBg;

vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}

float snoise(vec3 v){
  const vec2 C = vec2(1.0/6.0, 1.0/3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i  = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g  = step(x0.yzx, x0.xyz);
  vec3 l  = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(
              i.z + vec4(0.0, i1.z, i2.z, 1.0))
            + i.y + vec4(0.0, i1.y, i2.y, 1.0))
            + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0)*2.0+1.0;
  vec4 s1 = floor(b1)*2.0+1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
}

void main() {
  // Aspect-corrected UV, anchored to canvas centre.
  vec2 uv = vUv;
  uv.x *= uRes.x / uRes.y;

  // Mouse-warp: pull noise field gently toward the cursor.
  vec2 m = uMouse * vec2(uRes.x / uRes.y, 1.0);
  vec2 q = uv + 0.18 * (m - uv) * smoothstep(0.6, 0.0, distance(uv, m));

  float t = uTime * 0.18;

  // Layered noise → smooth blob field.
  float n1 = snoise(vec3(q * 1.4,        t));
  float n2 = snoise(vec3(q * 0.7 + 4.0,  t * 0.7 + 12.0));
  float n3 = snoise(vec3(q * 2.3 - 1.5,  t * 0.5 - 7.0));

  float a = smoothstep(-0.2, 0.7, n1);
  float b = smoothstep(-0.4, 0.6, n2);
  float c = smoothstep(-0.1, 0.8, n3);

  vec3 col = uBg;
  col = mix(col, uColorA, a * 0.55);
  col = mix(col, uColorB, b * 0.40);
  col = mix(col, uColorC, c * 0.30);

  // Soft vignette so the gradient feels seated in the card.
  float vign = smoothstep(1.2, 0.2, length(uv - vec2(uRes.x / uRes.y, 1.0) * 0.5));
  col *= mix(0.92, 1.0, vign);

  // Subtle film grain for depth (~1% strength).
  float grain = (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) * 0.018;
  col += grain;

  gl_FragColor = vec4(col, 1.0);
}
`;

interface Props {
  /** Background colour the gradient blends on top of. */
  bg?: [number, number, number];
  colorA?: [number, number, number];
  colorB?: [number, number, number];
  colorC?: [number, number, number];
  /** Multiplier on `uTime` so a tab can have slower / faster motion. */
  speed?: number;
  className?: string;
}

export function MeshGradient({
  bg     = [0.96, 0.96, 0.98],   // page bg
  colorA = [0.31, 0.27, 0.90],   // indigo
  colorB = [0.97, 0.78, 0.36],   // amber
  colorC = [0.83, 0.07, 0.67],   // magenta
  speed  = 1.0,
  className,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let stopped = false;
    let raf = 0;

    // Heavy deps lazy-imported so they never touch the SSR bundle.
    (async () => {
      const { Renderer, Program, Mesh, Triangle, Vec2, Vec3 } = await import("ogl");

      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      const renderer = new Renderer({
        dpr,
        alpha: false,
        antialias: false,
        powerPreference: "low-power",
      });
      const gl = renderer.gl;
      gl.canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block;";
      host.appendChild(gl.canvas);

      const geometry = new Triangle(gl);

      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      const program = new Program(gl, {
        vertex: VERT,
        fragment: FRAG,
        uniforms: {
          uTime:   { value: 0 },
          uRes:    { value: new Vec2(1, 1) },
          uMouse:  { value: new Vec2(0.5, 0.5) },
          uBg:     { value: new Vec3(...bg) },
          uColorA: { value: new Vec3(...colorA) },
          uColorB: { value: new Vec3(...colorB) },
          uColorC: { value: new Vec3(...colorC) },
        },
      });
      const mesh = new Mesh(gl, { geometry, program });

      function resize() {
        const r = host!.getBoundingClientRect();
        renderer.setSize(r.width, r.height);
        program.uniforms.uRes.value.set(r.width, r.height);
      }
      resize();
      const ro = new ResizeObserver(resize);
      ro.observe(host);

      // Mouse with smoothing.
      const target = new Vec2(0.5, 0.5);
      function onMouse(e: PointerEvent) {
        const r = host!.getBoundingClientRect();
        target.x = (e.clientX - r.left) / r.width;
        target.y = 1 - (e.clientY - r.top) / r.height;
      }
      window.addEventListener("pointermove", onMouse, { passive: true });

      const start = performance.now();
      let frozenOnce = false;

      function frame(now: number) {
        if (stopped) return;
        if (document.hidden) {
          raf = requestAnimationFrame(frame);
          return;
        }
        if (reduceMotion && frozenOnce) {
          raf = requestAnimationFrame(frame);
          return;
        }
        const t = ((now - start) / 1000) * speed;
        program.uniforms.uTime.value = t;
        program.uniforms.uMouse.value.x +=
          (target.x - program.uniforms.uMouse.value.x) * 0.05;
        program.uniforms.uMouse.value.y +=
          (target.y - program.uniforms.uMouse.value.y) * 0.05;
        renderer.render({ scene: mesh });
        if (reduceMotion) frozenOnce = true;
        raf = requestAnimationFrame(frame);
      }
      raf = requestAnimationFrame(frame);

      // Cleanup hook.
      (host as HTMLDivElement & { __cleanup?: () => void }).__cleanup = () => {
        stopped = true;
        cancelAnimationFrame(raf);
        ro.disconnect();
        window.removeEventListener("pointermove", onMouse);
        try { gl.getExtension("WEBGL_lose_context")?.loseContext(); } catch { /* noop */ }
        gl.canvas.remove();
      };
    })().catch((err) => {
      // WebGL not supported / OGL failed → leave host empty, parent
      // already has a CSS fallback gradient behind us.
      console.warn("[MeshGradient]", err);
    });

    return () => {
      const h = host as HTMLDivElement & { __cleanup?: () => void };
      h.__cleanup?.();
    };
  }, [bg, colorA, colorB, colorC, speed]);

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
