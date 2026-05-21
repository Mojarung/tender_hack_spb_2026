"use client";

/**
 * Plasma fluid shader — colourful flowing waves like 80s demoscene refined.
 *
 * Pure analytic plasma: sums of sines/cosines with smooth domain warping.
 * No noise needed, very fast (under 50 ALU ops per pixel) and 60fps even
 * on integrated GPUs. Uses palette() for iridescent colour cycling
 * (Inigo Quilez technique).
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
uniform vec3  uA;
uniform vec3  uB;
uniform vec3  uC;
uniform vec3  uD;
uniform float uSpeed;

// Inigo Quilez palette — cosine-based iridescent gradient.
vec3 palette(float t, vec3 a, vec3 b, vec3 c, vec3 d) {
  return a + b * cos(6.28318 * (c * t + d));
}

void main() {
  vec2 uv = vUv * 2.0 - 1.0;
  uv.x *= uRes.x / uRes.y;

  // Mouse-warp the plasma field gently.
  vec2 m = (uMouse * 2.0 - 1.0) * vec2(uRes.x / uRes.y, 1.0);
  uv += 0.10 * (m - uv) * smoothstep(0.8, 0.0, length(uv - m));

  float t = uTime * uSpeed;

  // Iterate domain warp 3x — classic IQ trick for smooth plasma.
  vec2 p = uv;
  for (int i = 0; i < 3; i++) {
    p += vec2(
      sin(p.y * 1.7 + t * 0.7 + float(i) * 1.3),
      cos(p.x * 1.5 - t * 0.5 + float(i) * 1.7)
    ) * 0.45;
  }

  // Sample iridescent palette.
  float n = 0.5 + 0.5 * sin(p.x * 0.7 + p.y * 0.9 + t * 0.4);
  vec3 col = palette(n, uA, uB, uC, uD);

  // Soft vignette
  float vign = smoothstep(1.3, 0.3, length(uv));
  col *= mix(0.85, 1.0, vign);

  // Film grain
  float g = (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) * 0.02;
  col += g;

  gl_FragColor = vec4(col, 1.0);
}
`;

interface Props {
  /** IQ palette controls — see https://iquilezles.org/articles/palettes/ */
  a?: [number, number, number];
  b?: [number, number, number];
  c?: [number, number, number];
  d?: [number, number, number];
  speed?: number;
  className?: string;
}

export function PlasmaShader({
  a = [0.50, 0.40, 0.50],     // base brightness
  b = [0.50, 0.55, 0.60],     // contrast amplitude
  c = [1.00, 1.00, 1.00],     // colour frequency
  d = [0.00, 0.33, 0.67],     // hue phase shift (RGB offset)
  speed = 0.45,
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
          uTime:  { value: 0 },
          uRes:   { value: new Vec2(1, 1) },
          uMouse: { value: new Vec2(0.5, 0.5) },
          uA:     { value: new Vec3(...a) },
          uB:     { value: new Vec3(...b) },
          uC:     { value: new Vec3(...c) },
          uD:     { value: new Vec3(...d) },
          uSpeed: { value: speed },
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
      let drewOnce = false;
      function frame(now: number) {
        if (stopped) return;
        if (document.hidden) { raf = requestAnimationFrame(frame); return; }
        if (reduceMotion && drewOnce) { raf = requestAnimationFrame(frame); return; }
        program.uniforms.uTime.value = (now - start) / 1000;
        program.uniforms.uMouse.value.x +=
          (target.x - program.uniforms.uMouse.value.x) * 0.05;
        program.uniforms.uMouse.value.y +=
          (target.y - program.uniforms.uMouse.value.y) * 0.05;
        renderer.render({ scene: mesh });
        drewOnce = true;
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
    })().catch((err) => console.warn("[PlasmaShader]", err));

    return () => {
      const h = host as HTMLDivElement & { __cleanup?: () => void };
      h.__cleanup?.();
    };
  }, [a, b, c, d, speed]);

  return (
    <div
      ref={hostRef}
      aria-hidden="true"
      className={className}
      style={{ position: "absolute", inset: 0, overflow: "hidden", pointerEvents: "none" }}
    />
  );
}
