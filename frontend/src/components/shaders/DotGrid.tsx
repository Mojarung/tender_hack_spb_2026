"use client";

/**
 * Interactive dot-grid background — optimised Canvas2D.
 *
 * Idle behaviour costs ~0 work. The naive version redrew all ~700 dots
 * every frame; this one:
 *
 *  1. Pre-renders the static grid once into an offscreen canvas.
 *     A single `drawImage` per frame replaces ~700 `arc()` calls.
 *  2. Maintains an `active` set — physics + custom drawing run ONLY
 *     for dots that are awake (mouse-near OR still oscillating).
 *     A dot that reaches its home is dropped from the set; it then
 *     comes back "for free" via the static layer.
 *  3. Skips the draw entirely when nothing has changed and the mouse
 *     hasn't moved (RAF keeps ticking but no canvas ops happen).
 *
 * Cleanup, DPR clamp, pause-on-hidden, spring + damping physics are
 * preserved from the original implementation.
 */

import { useEffect, useRef } from "react";

interface Props {
  spacing?: number;
  size?: number;
  pushRadius?: number;
  pushStrength?: number;
  springK?: number;
  damping?: number;
  color?: string;
  activeColor?: string;
}

interface RGBA { r: number; g: number; b: number; a: number; }

function parseRGBA(c: string): RGBA {
  const m = c.match(/rgba?\(([^)]+)\)/);
  if (!m) return { r: 11, g: 13, b: 18, a: 0.28 };
  const p = m[1].split(",").map((x) => parseFloat(x.trim()));
  return { r: p[0], g: p[1], b: p[2], a: p[3] ?? 1 };
}

export function DotGrid({
  spacing      = 30,
  size         = 1.5,
  pushRadius   = 110,
  pushStrength = 12,
  springK      = 0.085,
  damping      = 0.90,
  color        = "rgba(11, 13, 18, 0.28)",
  activeColor  = "rgba(79, 70, 229, 0.85)",
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    let stopped = false;
    let raf = 0;
    let w = 0, h = 0;
    let dpr = Math.min(window.devicePixelRatio || 1, 2);

    interface Dot { hx: number; hy: number; x: number; y: number; vx: number; vy: number; }
    let dots: Dot[] = [];
    let active = new Set<number>();

    // Mouse with "moved recently" flag so we can short-circuit ticks.
    const mouse = { x: -9999, y: -9999, present: false, dirty: false };

    const base = parseRGBA(color);
    const accent = parseRGBA(activeColor);
    const r2 = pushRadius * pushRadius;
    const restEps = 0.12;    // px — below this dots are considered home

    // ── offscreen static grid ──────────────────────────────────────────
    let staticCanvas: HTMLCanvasElement | null = null;

    function buildStatic() {
      staticCanvas = document.createElement("canvas");
      staticCanvas.width  = canvas!.width;
      staticCanvas.height = canvas!.height;
      const sctx = staticCanvas.getContext("2d");
      if (!sctx) return;
      sctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      sctx.fillStyle = `rgba(${base.r | 0}, ${base.g | 0}, ${base.b | 0}, ${base.a})`;
      for (let i = 0; i < dots.length; i++) {
        const d = dots[i];
        sctx.beginPath();
        sctx.arc(d.hx, d.hy, size, 0, Math.PI * 2);
        sctx.fill();
      }
    }

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
      active.clear();
      buildStatic();
      // First paint of the static layer onto the main canvas.
      ctx!.clearRect(0, 0, w, h);
      if (staticCanvas) ctx!.drawImage(staticCanvas, 0, 0, w, h);
    }
    rebuild();

    function onResize() { rebuild(); }
    window.addEventListener("resize", onResize);

    function onMove(e: PointerEvent) {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
      mouse.present = true;
      mouse.dirty = true;
    }
    function onLeave() {
      mouse.present = false;
      mouse.x = -9999; mouse.y = -9999;
      mouse.dirty = true;
    }
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerleave", onLeave);

    function frame() {
      if (stopped) return;
      if (document.hidden) { raf = requestAnimationFrame(frame); return; }

      // Mouse just moved → wake up neighbours.
      if (mouse.dirty && mouse.present) {
        // Tight bbox around the cursor; only check dots inside it.
        const minX = mouse.x - pushRadius, maxX = mouse.x + pushRadius;
        const minY = mouse.y - pushRadius, maxY = mouse.y + pushRadius;
        for (let i = 0; i < dots.length; i++) {
          const d = dots[i];
          if (d.hx < minX || d.hx > maxX || d.hy < minY || d.hy > maxY) continue;
          const dx = d.x - mouse.x;
          const dy = d.y - mouse.y;
          if (dx * dx + dy * dy < r2) active.add(i);
        }
        mouse.dirty = false;
      }

      if (active.size === 0) {
        raf = requestAnimationFrame(frame);
        return;          // ← entire frame becomes a no-op while idle
      }

      // Step physics for active dots only, collect bounding region
      // so we can repair the previous frame with one drawImage().
      let bbMinX =  Infinity, bbMinY =  Infinity;
      let bbMaxX = -Infinity, bbMaxY = -Infinity;
      const finished: number[] = [];

      for (const i of active) {
        const d = dots[i];
        const px = d.x, py = d.y;

        if (mouse.present) {
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
        d.vx += (d.hx - d.x) * springK;
        d.vy += (d.hy - d.y) * springK;
        d.vx *= damping;
        d.vy *= damping;
        d.x  += d.vx;
        d.y  += d.vy;

        // Expand dirty bbox to cover both old & new positions.
        const pad = size + 4;
        if (px - pad < bbMinX) bbMinX = px - pad;
        if (py - pad < bbMinY) bbMinY = py - pad;
        if (px + pad > bbMaxX) bbMaxX = px + pad;
        if (py + pad > bbMaxY) bbMaxY = py + pad;
        if (d.x - pad < bbMinX) bbMinX = d.x - pad;
        if (d.y - pad < bbMinY) bbMinY = d.y - pad;
        if (d.x + pad > bbMaxX) bbMaxX = d.x + pad;
        if (d.y + pad > bbMaxY) bbMaxY = d.y + pad;

        // Settled? evict from active set; snap exactly home for crispness.
        const dxh = d.x - d.hx, dyh = d.y - d.hy;
        if (Math.abs(d.vx) < restEps && Math.abs(d.vy) < restEps &&
            Math.abs(dxh) < restEps && Math.abs(dyh) < restEps &&
            (!mouse.present || (d.hx - mouse.x) ** 2 + (d.hy - mouse.y) ** 2 > r2)) {
          d.x = d.hx; d.y = d.hy; d.vx = 0; d.vy = 0;
          finished.push(i);
        }
      }
      for (const i of finished) active.delete(i);

      // Repair last frame within bbox using the static layer.
      const dx = Math.max(0, Math.floor(bbMinX));
      const dy = Math.max(0, Math.floor(bbMinY));
      const dw = Math.min(w,  Math.ceil(bbMaxX)) - dx;
      const dh = Math.min(h,  Math.ceil(bbMaxY)) - dy;
      if (dw > 0 && dh > 0 && staticCanvas) {
        ctx!.clearRect(dx, dy, dw, dh);
        ctx!.drawImage(
          staticCanvas,
          dx * dpr, dy * dpr, dw * dpr, dh * dpr,
          dx,       dy,       dw,       dh,
        );
      }

      // Draw active dots over the repaired patch.
      for (const i of active) {
        const d = dots[i];
        const ddx = d.x - d.hx, ddy = d.y - d.hy;
        const disp = Math.min(Math.sqrt(ddx * ddx + ddy * ddy) / 40, 1);
        const r = base.r + (accent.r - base.r) * disp;
        const g = base.g + (accent.g - base.g) * disp;
        const b = base.b + (accent.b - base.b) * disp;
        const a = base.a + (accent.a - base.a) * disp;
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
      staticCanvas = null;
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
