"use client";

/**
 * Interactive dot-grid background.
 *
 * Plain Canvas2D — much cheaper than a fragment shader for this kind of
 * physics-driven effect, and keeps the rest of the page on a clean
 * #fafafb surface.
 *
 * Each dot has a home position; mouse pushes them away with an
 * inverse-distance falloff, springs pull them back home. Damping keeps
 * the motion calm. 60 fps even with ~700 dots on a 1440×900 viewport.
 */

import { useEffect, useRef } from "react";

interface Props {
  /** Grid spacing in CSS pixels. Smaller = denser. */
  spacing?: number;
  /** Dot radius in CSS pixels. */
  size?: number;
  /** Mouse influence radius in CSS pixels. */
  pushRadius?: number;
  /** How hard the cursor shoves dots away. */
  pushStrength?: number;
  /** Spring constant pulling each dot back to its home. */
  springK?: number;
  /** Per-frame velocity decay (0–1; 0.86 = lively, 0.95 = floaty). */
  damping?: number;
  /** Dot colour. */
  color?: string;
  /** Dot colour at peak displacement (lerps from `color`). Optional. */
  activeColor?: string;
}

export function DotGrid({
  spacing      = 28,
  size         = 1.6,
  pushRadius   = 140,
  pushStrength = 28,
  springK      = 0.055,
  damping      = 0.86,
  color        = "rgba(11, 13, 18, 0.30)",
  activeColor  = "rgba(79, 70, 229, 0.95)",
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let stopped = false;
    let raf = 0;
    let w = 0, h = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);

    interface Dot { hx: number; hy: number; x: number; y: number; vx: number; vy: number; }
    let dots: Dot[] = [];

    const mouse = { x: -9999, y: -9999, active: false };

    function rebuild() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas!.width  = Math.floor(w * dpr);
      canvas!.height = Math.floor(h * dpr);
      canvas!.style.width  = `${w}px`;
      canvas!.style.height = `${h}px`;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      const cols = Math.floor(w / spacing) + 2;
      const rows = Math.floor(h / spacing) + 2;
      const offX = (w - (cols - 1) * spacing) / 2;
      const offY = (h - (rows - 1) * spacing) / 2;

      dots = new Array(cols * rows);
      let i = 0;
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const hx = offX + c * spacing;
          const hy = offY + r * spacing;
          dots[i++] = { hx, hy, x: hx, y: hy, vx: 0, vy: 0 };
        }
      }
    }
    rebuild();

    function onResize() { rebuild(); }
    window.addEventListener("resize", onResize);

    function onMove(e: PointerEvent) {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
      mouse.active = true;
    }
    function onLeave() { mouse.active = false; mouse.x = -9999; mouse.y = -9999; }
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerleave", onLeave);

    // Parse the two colours once and lerp in RGB space for cheap blending.
    function parseRGBA(c: string): [number, number, number, number] {
      const m = c.match(/rgba?\(([^)]+)\)/);
      if (!m) return [11, 13, 18, 0.3];
      const parts = m[1].split(",").map((p) => parseFloat(p.trim()));
      return [parts[0], parts[1], parts[2], parts[3] ?? 1];
    }
    const baseRGBA   = parseRGBA(color);
    const activeRGBA = parseRGBA(activeColor);
    const r2 = pushRadius * pushRadius;

    function frame() {
      if (stopped) return;
      if (document.hidden) { raf = requestAnimationFrame(frame); return; }

      ctx!.clearRect(0, 0, w, h);

      for (let i = 0; i < dots.length; i++) {
        const d = dots[i];

        if (mouse.active) {
          const dx = d.x - mouse.x;
          const dy = d.y - mouse.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < r2 && d2 > 0.0001) {
            const dist = Math.sqrt(d2);
            const force = ((pushRadius - dist) / pushRadius) * pushStrength;
            d.vx += (dx / dist) * force * 0.1;
            d.vy += (dy / dist) * force * 0.1;
          }
        }

        // spring back home
        d.vx += (d.hx - d.x) * springK;
        d.vy += (d.hy - d.y) * springK;
        d.vx *= damping;
        d.vy *= damping;
        d.x  += d.vx;
        d.y  += d.vy;

        // displacement-driven colour & size
        const ddx = d.x - d.hx, ddy = d.y - d.hy;
        const disp = Math.min(Math.sqrt(ddx * ddx + ddy * ddy) / 40, 1);
        const r = baseRGBA[0] + (activeRGBA[0] - baseRGBA[0]) * disp;
        const g = baseRGBA[1] + (activeRGBA[1] - baseRGBA[1]) * disp;
        const b = baseRGBA[2] + (activeRGBA[2] - baseRGBA[2]) * disp;
        const a = baseRGBA[3] + (activeRGBA[3] - baseRGBA[3]) * disp;
        const rad = size + disp * 2.2;

        ctx!.beginPath();
        ctx!.fillStyle = `rgba(${r | 0}, ${g | 0}, ${b | 0}, ${a})`;
        ctx!.arc(d.x, d.y, rad, 0, Math.PI * 2);
        ctx!.fill();
      }

      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);

    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerleave", onLeave);
    };
  }, [spacing, size, pushRadius, pushStrength, springK, damping, color, activeColor]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        width: "100vw",
        height: "100vh",
        pointerEvents: "none",
        zIndex: 0,
      }}
    />
  );
}
