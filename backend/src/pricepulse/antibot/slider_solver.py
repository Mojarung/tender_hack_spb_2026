"""Slider/puzzle CAPTCHA solver via OpenCV.

Geometry-only: no LLM, no network. Solves Ozon slider, Geetest, DataDome
puzzle, TikTok and other "drag the piece into the hole" captchas in ~50ms
on CPU. Port of vsmutok/PuzzleCaptchaSolver (MIT, active May 2026).

Pipeline:
  1. Crop whitespace from both images.
  2. Canny edge detection on both — exposes shape contour, removes texture
     noise that ruins template matching.
  3. cv2.matchTemplate(method=TM_CCOEFF_NORMED) finds where the gap piece
     correlates best with the background. That position is the X offset
     the slider needs to be dragged to.

Usage:
    x_offset = solve_slider(background_bytes, gap_bytes)
    # then in Patchright: drag the slider knob by `x_offset` px with
    # humancursor bezier — antibot scoring stays happy.
"""

from __future__ import annotations

import io

import cv2
import numpy as np


def _decode(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Could not decode image bytes")
    return img


def _remove_whitespace(img: np.ndarray, threshold: int = 250) -> np.ndarray:
    """Trim transparent/white borders from the gap-piece sprite."""
    if img.shape[2] == 4:
        alpha = img[:, :, 3]
        mask = alpha > 0
    else:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mask = gray < threshold
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return img
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    return img[y0:y1 + 1, x0:x1 + 1]


def _edges(img: np.ndarray, low: int = 100, high: int = 200) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.Canny(gray, low, high)


def solve_slider(background_bytes: bytes, gap_bytes: bytes) -> int:
    """Return X-coordinate (in px) where the slider piece should land.

    Both inputs are raw image bytes (PNG/JPEG/WEBP). The returned X is
    measured from the LEFT edge of `background_bytes`.
    """
    bg = _decode(background_bytes)
    if bg.shape[2] == 4:
        bg = cv2.cvtColor(bg, cv2.COLOR_BGRA2BGR)

    gap = _remove_whitespace(_decode(gap_bytes))
    if gap.shape[2] == 4:
        gap = cv2.cvtColor(gap, cv2.COLOR_BGRA2BGR)

    bg_edges = _edges(bg)
    gap_edges = _edges(gap)

    result = cv2.matchTemplate(bg_edges, gap_edges, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(result)
    return int(max_loc[0])


def annotate_match(
    background_bytes: bytes, gap_bytes: bytes, color: tuple[int, int, int] = (0, 0, 255)
) -> bytes:
    """Debug helper: draws a rectangle around the match. Returns PNG bytes."""
    bg = _decode(background_bytes)
    if bg.shape[2] == 4:
        bg = cv2.cvtColor(bg, cv2.COLOR_BGRA2BGR)
    gap = _remove_whitespace(_decode(gap_bytes))
    if gap.shape[2] == 4:
        gap = cv2.cvtColor(gap, cv2.COLOR_BGRA2BGR)

    bg_edges = _edges(bg)
    gap_edges = _edges(gap)
    result = cv2.matchTemplate(bg_edges, gap_edges, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(result)
    h, w = gap_edges.shape
    top_left = max_loc
    bottom_right = (top_left[0] + w, top_left[1] + h)
    cv2.rectangle(bg, top_left, bottom_right, color, 2)
    success, buf = cv2.imencode(".png", bg)
    if not success:
        raise RuntimeError("PNG encode failed")
    return io.BytesIO(buf.tobytes()).getvalue()
