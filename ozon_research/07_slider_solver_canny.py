"""07 — slider CAPTCHA solver, Canny + matchTemplate variant.

PURPOSE
    The production `antibot/slider_solver.py` uses plain template
    matching. Three independent 2025-2026 references converge on:
        1. crop the puzzle piece to its non-transparent bbox
           (whitespace removal)
        2. Canny edge-detect BOTH background and piece
        3. cv2.matchTemplate(TM_CCOEFF_NORMED)
        4. cv2.minMaxLoc → max_loc.x = drag offset
    Edge-first beats raw cross-correlation under alpha-blended shadow.

    This script:
      - solves a SYNTHETIC slider (we generate it ourselves so the
        ground-truth offset is known and you can confirm it works
        WITHOUT a live Ozon CAPTCHA)
      - also accepts a path to a real bg/piece pair if you save them
        from a live Ozon challenge.

USAGE
    cd ozon_research
    # Synthetic test (always runs):
    uv run python 07_slider_solver_canny.py

    # Real Ozon CAPTCHA images you've grabbed:
    uv run python 07_slider_solver_canny.py path/to/bg.png path/to/piece.png

OUTPUT
    Prints the detected X offset. Synthetic ground-truth check passes
    if |detected - true| <= 4 px (Ozon's tolerance is ~5 px in practice).
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import err, info, ok, save_bytes, section, warn

# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------
def find_gap_x(bg_png: bytes, piece_png: bytes, *, return_score: bool = False):
    """Return the X-offset (px) in the background where the piece fits.

    Approach: whitespace-crop piece → Canny both → TM_CCOEFF_NORMED.
    """
    import cv2
    import numpy as np

    bg = cv2.imdecode(np.frombuffer(bg_png, np.uint8), cv2.IMREAD_GRAYSCALE)
    pc = cv2.imdecode(np.frombuffer(piece_png, np.uint8), cv2.IMREAD_UNCHANGED)
    if bg is None or pc is None:
        raise ValueError("could not decode images")

    # Crop piece to non-transparent bbox if there's an alpha channel
    if pc.ndim == 3 and pc.shape[2] == 4:
        alpha = pc[:, :, 3]
        coords = cv2.findNonZero(alpha)
        if coords is not None:
            x, y, w, h = cv2.boundingRect(coords)
            pc = pc[y : y + h, x : x + w]
        pc = cv2.cvtColor(pc, cv2.COLOR_BGRA2GRAY)
    elif pc.ndim == 3:
        pc = cv2.cvtColor(pc, cv2.COLOR_BGR2GRAY)

    bg_e = cv2.Canny(bg, 100, 200)
    pc_e = cv2.Canny(pc, 100, 200)
    res = cv2.matchTemplate(bg_e, pc_e, cv2.TM_CCOEFF_NORMED)
    _, score, _, max_loc = cv2.minMaxLoc(res)
    return (max_loc[0], score) if return_score else max_loc[0]


# ---------------------------------------------------------------------------
# Synthetic test rig
# ---------------------------------------------------------------------------
def _synth_pair(true_offset: int = 187, width: int = 360, height: int = 160, piece_size: int = 60):
    """Generate a realistic-ish slider pair: background with a darker
    puzzle-piece-shaped gap at `true_offset`, and a separate piece sprite."""
    import cv2
    import numpy as np

    rng = np.random.default_rng(seed=42)
    # Background — soft gradient + low-amplitude noise (looks like a
    # photograph of a product, which is what Ozon uses).
    base = np.linspace(60, 200, width, dtype=np.uint8)
    bg = np.tile(base, (height, 1))
    bg = bg + rng.integers(-15, 15, size=bg.shape, dtype=np.int16)
    bg = np.clip(bg, 0, 255).astype(np.uint8)
    # Add a few darker blobs to mimic objects in the photo
    for _ in range(8):
        cx, cy = rng.integers(10, width - 10), rng.integers(10, height - 10)
        r = rng.integers(5, 20)
        cv2.circle(bg, (int(cx), int(cy)), int(r), int(rng.integers(20, 80)), -1)

    # Carve a darker "puzzle-piece" gap at true_offset
    gap = np.zeros((piece_size, piece_size), dtype=np.uint8)
    cv2.rectangle(gap, (5, 5), (piece_size - 5, piece_size - 5), 255, -1)
    cv2.circle(gap, (piece_size // 2, 5), 8, 0, -1)  # notch
    cv2.circle(gap, (piece_size - 5, piece_size // 2), 8, 255, -1)  # bump

    y0 = (height - piece_size) // 2
    bg_with_gap = bg.copy()
    region = bg_with_gap[y0 : y0 + piece_size, true_offset : true_offset + piece_size]
    # Subtract gap shape (darker pixels where gap mask is set)
    region[gap > 0] = np.clip(region[gap > 0].astype(np.int16) - 80, 0, 255).astype(np.uint8)

    # Piece sprite — same shape with alpha (transparent outside)
    piece_rgba = np.zeros((piece_size, piece_size, 4), dtype=np.uint8)
    piece_rgba[..., 0:3] = bg[y0 : y0 + piece_size, true_offset : true_offset + piece_size, None]
    piece_rgba[..., 3] = gap

    _, bg_png = cv2.imencode(".png", bg_with_gap)
    _, pc_png = cv2.imencode(".png", piece_rgba)
    return bg_png.tobytes(), pc_png.tobytes(), true_offset


def _run_synth() -> int:
    info("running synthetic test (true offset = 187)...")
    try:
        bg, pc, true_x = _synth_pair()
    except ImportError as exc:
        err(f"opencv/numpy not installed: {exc}")
        return 3

    saved_bg = save_bytes("07_synth_bg", bg, ".png")
    saved_pc = save_bytes("07_synth_piece", pc, ".png")
    info(f"synthetic images saved: {saved_bg.name}, {saved_pc.name}")

    detected, score = find_gap_x(bg, pc, return_score=True)
    info(f"detected = {detected} px (score={score:.3f})")
    info(f"true     = {true_x} px")
    delta = abs(detected - true_x)
    if delta <= 4:
        ok(f"PASS — within 4 px tolerance (delta={delta})")
        return 0
    err(f"FAIL — delta={delta} px exceeds 4 px tolerance")
    return 1


def _run_real(bg_path: Path, piece_path: Path) -> int:
    info(f"bg    = {bg_path}")
    info(f"piece = {piece_path}")
    if not bg_path.exists() or not piece_path.exists():
        err("one of the image paths does not exist")
        return 3
    detected, score = find_gap_x(bg_path.read_bytes(), piece_path.read_bytes(), return_score=True)
    ok(f"detected offset = {detected} px (correlation score = {score:.3f})")
    if score < 0.3:
        warn("low score — Canny may be losing edges; try cv2.TM_SQDIFF_NORMED + minMaxLoc.min_loc")
    return 0


def main() -> int:
    section("SLIDER SOLVER — Canny edges + TM_CCOEFF_NORMED")

    if len(sys.argv) == 1:
        return _run_synth()
    if len(sys.argv) == 3:
        return _run_real(Path(sys.argv[1]), Path(sys.argv[2]))
    err("usage: python 07_slider_solver_canny.py [bg.png piece.png]")
    return 3


if __name__ == "__main__":
    sys.exit(main())
