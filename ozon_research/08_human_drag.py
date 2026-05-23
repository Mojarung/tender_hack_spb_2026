"""08 — human-like drag generator (offline; no browser needed).

PURPOSE
    Behavioural CAPTCHA scoring penalises:
      - linear, constant-velocity drags
      - drags without overshoot/correction
      - drags released within 0 ms of reaching target
    Public reverse-engineering of GeeTest/Ozon-style sliders (see Habr
    Apr 2026, scrapfly stealth guides) converges on a cubic-Bezier
    track with:
      - acceleration phase
      - overshoot 8-18 px beyond the true target
      - micro-correction back
      - hold 50-150 ms before mouseup

This script does NOT touch a browser. It just dumps the (x, y, dt_ms)
event list so you can inspect/plot the trajectory and decide whether
it's plausible BEFORE wiring it into Patchright's page.mouse.move().

USAGE
    cd ozon_research
    uv run python 08_human_drag.py [target_dx_px=180]

OUTPUT
    A CSV in _out/ with columns: t_ms,x,y,phase
    Eyeball it (Excel/plot) — velocity profile should be S-curved with
    a clear overshoot bump near the end.
"""

from __future__ import annotations

import math
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import OUT_DIR, info, ok, section, warn


def _bezier(t: float, p0: tuple[float, float], p1: tuple[float, float],
            p2: tuple[float, float], p3: tuple[float, float]) -> tuple[float, float]:
    """Cubic Bezier at parameter t∈[0,1]."""
    u = 1 - t
    x = (u**3) * p0[0] + 3 * (u**2) * t * p1[0] + 3 * u * (t**2) * p2[0] + (t**3) * p3[0]
    y = (u**3) * p0[1] + 3 * (u**2) * t * p1[1] + 3 * u * (t**2) * p2[1] + (t**3) * p3[1]
    return x, y


def human_drag_track(
    start_x: float,
    start_y: float,
    target_dx: float,
    *,
    duration_ms: int = 900,
    fps: int = 60,
    overshoot_min: int = 8,
    overshoot_max: int = 18,
    release_hold_ms: tuple[int, int] = (50, 150),
    seed: int | None = None,
) -> list[tuple[int, float, float, str]]:
    """Produce [(t_ms, x, y, phase), ...] for the full drag.

    `phase` ∈ {"down", "move", "correct", "hold", "up"} so the caller
    knows where to fire mousedown / mouseup vs movemoves.
    """
    rng = random.Random(seed)
    overshoot_px = rng.randint(overshoot_min, overshoot_max)
    p0 = (start_x, start_y)
    p1 = (start_x + (target_dx + overshoot_px) * 0.30, start_y - rng.uniform(2, 6))
    p2 = (start_x + (target_dx + overshoot_px) * 0.70, start_y + rng.uniform(1, 4))
    p3 = (start_x + target_dx + overshoot_px, start_y + rng.uniform(-1, 1))

    steps = max(20, int(duration_ms * fps / 1000))
    out: list[tuple[int, float, float, str]] = []
    out.append((0, start_x, start_y, "down"))

    # main bezier traversal with eased-out cubic (slow in, fast then slow out)
    t_acc = 0
    for i in range(1, steps + 1):
        u = i / steps
        # eased-out cubic
        e = 1 - (1 - u) ** 3
        x, y = _bezier(e, p0, p1, p2, p3)
        # micro-jitter (gaussian ~0.4 px)
        x += rng.gauss(0, 0.4)
        y += rng.gauss(0, 0.4)
        # frame interval: 1000/fps ± 2 ms
        dt = int(1000 / fps + rng.uniform(-2, 2))
        t_acc += dt
        out.append((t_acc, x, y, "move"))

    # correction phase — walk back from overshoot to true target over ~5 frames
    correct_target_x = start_x + target_dx
    last_x, last_y = out[-1][1], out[-1][2]
    for i in range(5):
        u = (i + 1) / 5
        x = last_x + (correct_target_x - last_x) * u
        y = last_y + rng.gauss(0, 0.3)
        dt = rng.randint(12, 22)
        t_acc += dt
        out.append((t_acc, x, y, "correct"))

    # release-hold
    hold_ms = rng.randint(*release_hold_ms)
    t_acc += hold_ms
    out.append((t_acc, out[-1][1], out[-1][2], "hold"))
    out.append((t_acc, out[-1][1], out[-1][2], "up"))
    return out


def _summary(track: list[tuple[int, float, float, str]]) -> dict:
    total_ms = track[-1][0]
    moves = [(t, x, y) for t, x, y, p in track if p in {"move", "correct"}]
    if len(moves) < 2:
        return {"total_ms": total_ms}
    velocities = []
    for (t0, x0, _), (t1, x1, _) in zip(moves, moves[1:]):
        dt = (t1 - t0) / 1000
        if dt > 0:
            velocities.append((x1 - x0) / dt)
    return {
        "total_ms": total_ms,
        "n_events": len(track),
        "n_moves": len(moves),
        "max_velocity_pxs": round(max(velocities), 1) if velocities else None,
        "mean_velocity_pxs": round(sum(velocities) / len(velocities), 1) if velocities else None,
    }


def main() -> int:
    section("HUMAN DRAG TRACK — cubic-Bezier with overshoot + jitter + release-hold")

    dx = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    info(f"target_dx = {dx} px")
    info(f"duration  = 900 ms, fps = 60, overshoot = 8-18 px, release-hold = 50-150 ms")

    track = human_drag_track(start_x=12.0, start_y=42.0, target_dx=dx, seed=42)
    s = _summary(track)
    info(f"events    = {s['n_events']} (moves {s['n_moves']}, total {s['total_ms']} ms)")
    info(f"velocity  = mean {s['mean_velocity_pxs']} px/s, peak {s['max_velocity_pxs']} px/s")

    if s["max_velocity_pxs"] is None or s["max_velocity_pxs"] < 50:
        warn("peak velocity is suspiciously low — track may look robotic")
    if s["max_velocity_pxs"] and s["max_velocity_pxs"] > 4000:
        warn("peak velocity too high — looks like a teleport")

    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    out = OUT_DIR / f"{stamp}_08_drag_track_dx{dx}.csv"
    with out.open("w", encoding="utf-8") as f:
        f.write("t_ms,x,y,phase\n")
        for t, x, y, p in track:
            f.write(f"{t},{x:.3f},{y:.3f},{p}\n")
    ok(f"track CSV → {out}")
    info("→ pipe this CSV into Playwright/Patchright's page.mouse loop, or visualise in Excel/matplotlib")
    return 0


if __name__ == "__main__":
    sys.exit(main())
