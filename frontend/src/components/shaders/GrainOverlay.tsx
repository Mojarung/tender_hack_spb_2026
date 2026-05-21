"use client";

/**
 * Site-wide grain overlay — a fixed full-viewport layer with a very faint
 * animated noise pattern. Adds texture to flat surfaces (like Stripe /
 * Apple / Vercel landing pages).
 *
 * Hard-wired to be cheap: 32 fps cap, DPR=1, 2% opacity. Acts as a sibling
 * to <main>, mix-blend-mode: overlay.
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

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

void main() {
  // Slowly drifting noise so the grain doesn't look static like an
  // image overlay, but doesn't scream "look at me" either.
  vec2 px = gl_FragCoord.xy + vec2(uTime * 8.0, uTime * 5.0);
  float n = hash(floor(px));
  gl_FragColor = vec4(vec3(n), 0.5);   // alpha multiplied by host CSS
}
`;

export function GrainOverlay({ opacity = 0.035 }: { opacity?: number }) {
  const hostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let stopped = false;
    let raf = 0;

    (async () => {
      const { Renderer, Program, Mesh, Triangle, Vec2 } = await import("ogl");

      const renderer = new Renderer({
        dpr: 1,                // crisper grain at native pixels is overkill
        alpha: true,
        antialias: false,
        powerPreference: "low-power",
      });
      const gl = renderer.gl;
      gl.canvas.style.cssText =
        "position:fixed;inset:0;width:100vw;height:100vh;display:block;pointer-events:none;";
      host.appendChild(gl.canvas);

      const program = new Program(gl, {
        vertex: VERT,
        fragment: FRAG,
        transparent: true,
        uniforms: {
          uTime: { value: 0 },
          uRes:  { value: new Vec2(window.innerWidth, window.innerHeight) },
        },
      });
      const mesh = new Mesh(gl, { geometry: new Triangle(gl), program });

      function resize() {
        renderer.setSize(window.innerWidth, window.innerHeight);
        program.uniforms.uRes.value.set(window.innerWidth, window.innerHeight);
      }
      resize();
      window.addEventListener("resize", resize);

      // Render at ~30 fps — perfectly enough for grain.
      const interval = 1000 / 30;
      let last = 0;
      const start = performance.now();
      function frame(now: number) {
        if (stopped) return;
        if (document.hidden) { raf = requestAnimationFrame(frame); return; }
        if (now - last >= interval) {
          program.uniforms.uTime.value = (now - start) / 1000;
          renderer.render({ scene: mesh });
          last = now;
        }
        raf = requestAnimationFrame(frame);
      }
      raf = requestAnimationFrame(frame);

      (host as HTMLDivElement & { __cleanup?: () => void }).__cleanup = () => {
        stopped = true;
        cancelAnimationFrame(raf);
        window.removeEventListener("resize", resize);
        try { gl.getExtension("WEBGL_lose_context")?.loseContext(); } catch { /* noop */ }
        gl.canvas.remove();
      };
    })().catch((err) => console.warn("[GrainOverlay]", err));

    return () => {
      const h = host as HTMLDivElement & { __cleanup?: () => void };
      h.__cleanup?.();
    };
  }, []);

  return (
    <div
      ref={hostRef}
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        pointerEvents: "none",
        opacity,
        mixBlendMode: "overlay",
        zIndex: 100,
      }}
    />
  );
}
