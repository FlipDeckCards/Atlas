"""Sprite sheets in the standard layout: one row per animation, a bold label on the left, the same number of frames in every row, on a warm
gradient background.

The image model draws one strip per animation (N frames of the attached character in a row, on a flat magenta background). This module
turns each strip into frames (background removed, split into N, scaled by one common factor so the character is the same size in every
frame, feet on a common baseline) and lays them out two ways:

  * the labelled sheet (the template the person asked for): labels, gradient, evenly spaced frames;
  * a clean sheet for a game engine: the same grid with no labels and a transparent background.
"""

from __future__ import annotations

import io
from typing import Dict, List, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from openjarvis.server.crew.imageedit import _font

DEFAULT_ANIMATIONS = ("Idle", "Walk", "Run", "Jump", "Attack", "Hurt", "Death")
DEFAULT_FRAMES, MIN_FRAMES, MAX_FRAMES, MAX_FRAMES_UNLIMITED = 7, 2, 12, 16
MAX_ROWS, MAX_ROWS_UNLIMITED = 10, 30
CELL = 256                                   # pixels per frame cell in both sheets
LABEL_W = 360                                # the label column of the labelled sheet
PAD = 40
KEY_NEAR, KEY_FAR = 70.0, 140.0              # colour distance from the background: fully clear below KEY_NEAR, fully kept above KEY_FAR

ANIMATION_HINTS = {
    "idle": "standing still, breathing gently (small up and down movement, weapon held ready)",
    "walk": "a walk cycle moving in the facing direction, legs alternating",
    "run": "a run cycle, leaning forward, legs and arms in a fast stride",
    "jump": "a jump: crouch, take off, rise, peak, fall, land",
    "attack": "an attack: wind up, swing or strike with the character's weapon or fists, follow through, recover",
    "hurt": "getting hit: flinch back, recoil, then recover",
    "death": "a death animation: stagger, fall and end lying on the ground",
}


def clean_animations(value, unlimited: bool = False) -> List[str]:
    names = [" ".join(str(v).split())[:24] for v in (value if isinstance(value, list) else []) if str(v).strip()]
    return (names or list(DEFAULT_ANIMATIONS))[: MAX_ROWS_UNLIMITED if unlimited else MAX_ROWS]


def clean_frames(value, unlimited: bool = False) -> int:
    try:
        n = int(value) if value is not None else DEFAULT_FRAMES
    except (TypeError, ValueError):
        n = DEFAULT_FRAMES
    return max(MIN_FRAMES, min(MAX_FRAMES_UNLIMITED if unlimited else MAX_FRAMES, n))


def strip_prompt(animation: str, frames: int, notes: str = "") -> str:
    hint = ANIMATION_HINTS.get(animation.strip().lower(), f"the '{animation}' animation")
    return (
        f"Use the attached character as the exact reference: the same design, colours, proportions, outfit and art style. "
        f"Draw a sprite-sheet animation strip of this character: exactly {frames} frames in ONE horizontal row, left to right, "
        f"showing {hint}. The motion must progress smoothly from the first frame to the last. "
        f"Every frame is the same size, evenly spaced, with the whole character visible and the feet on the same ground line, "
        f"and the character faces the same direction as in the reference. "
        f"Plain solid flat magenta (#FF00FF) background everywhere. No text, no labels, no numbers, no grid lines, no borders, "
        f"no ground shadow, no frame boxes, nothing overlapping between frames."
        + (f" Extra notes from the person: {notes}" if notes else "")
    )


# ── one strip -> frames ────────────────────────────────────────────────────────────────────────

def key_out_background(data: bytes) -> Image.Image:
    """RGBA with the flat background made transparent (the background colour is read from the picture's border)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.asarray(img, dtype=float)
    ring = np.concatenate([arr[:4].reshape(-1, 3), arr[-4:].reshape(-1, 3), arr[:, :4].reshape(-1, 3), arr[:, -4:].reshape(-1, 3)])
    bg = np.median(ring, axis=0)
    dist = np.sqrt(((arr - bg) ** 2).sum(axis=2))
    alpha = np.clip((dist - KEY_NEAR) / (KEY_FAR - KEY_NEAR), 0, 1)
    # a magenta fringe on soft edges: pull the colour away from the key colour where the pixel is only partly kept
    part = (alpha > 0) & (alpha < 1)
    rgb = arr.copy()
    rgb[part] = np.clip((arr[part] - bg * (1 - alpha[part, None])) / np.maximum(alpha[part, None], 0.2), 0, 255)
    out = np.dstack([rgb, alpha * 255]).astype("uint8")
    return Image.fromarray(out, "RGBA")


def _bbox(img: Image.Image) -> Tuple[int, int, int, int]:
    box = img.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
    return box or (0, 0, img.width, img.height)


def split_strip(strip: Image.Image, n: int) -> List[Image.Image]:
    """n RGBA frames, each cropped to the character. Found by the gaps between the figures; when the gaps do not give exactly n figures
    the strip is cut into n equal cells instead."""
    alpha = np.asarray(strip.getchannel("A")) > 40
    cols = alpha.sum(axis=0) > 1
    runs, start = [], None
    for i, c in enumerate(list(cols) + [False]):
        if c and start is None:
            start = i
        elif not c and start is not None:
            runs.append([start, i])
            start = None
    merged: List[List[int]] = []
    for a, b in runs:                                           # a thin gap inside one figure (a sword, a cape) is not a boundary
        if merged and a - merged[-1][1] < strip.width * 0.012:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    merged = [m for m in merged if m[1] - m[0] >= strip.width * 0.02]
    if len(merged) != n:
        w = strip.width / n
        merged = [[int(i * w), int((i + 1) * w)] for i in range(n)]
    frames = []
    for a, b in merged:
        cell = strip.crop((a, 0, b, strip.height))
        frames.append(cell.crop(_bbox(cell)))
    return frames


# ── frames -> sheets ───────────────────────────────────────────────────────────────────────────

def _scale(rows: Sequence[Sequence[Image.Image]]) -> float:
    """One scale for the whole sheet, so the character is the same size in every frame."""
    mw = max(f.width for r in rows for f in r)
    mh = max(f.height for r in rows for f in r)
    return min(CELL * 0.9 / mw, CELL * 0.86 / mh)


def _place(canvas: Image.Image, frame: Image.Image, scale: float, x0: int, y0: int) -> None:
    f = frame.resize((max(1, round(frame.width * scale)), max(1, round(frame.height * scale))), Image.LANCZOS)
    canvas.alpha_composite(f, (x0 + (CELL - f.width) // 2, y0 + int(CELL * 0.95) - f.height))      # centred, feet on a common baseline


def _gradient(w: int, h: int) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / 1.2
    t = np.clip(d, 0, 1)[..., None]
    centre, edge = np.array([255, 249, 170.0]), np.array([232, 188, 58.0])
    return Image.fromarray(np.dstack([centre * (1 - t) + edge * t, np.full((h, w), 255.0)]).astype("uint8"), "RGBA")


def _label_font(name: str):
    size = 92
    f = _font(size)
    while f.getlength(name) > LABEL_W - PAD and size > 28:        # a long name is made smaller to fit the label column
        size -= 4
        f = _font(size)
    return f, size


def labelled_sheet(rows: Dict[str, List[Image.Image]]) -> bytes:
    names = list(rows)
    cols = max(len(r) for r in rows.values())
    w, h = LABEL_W + cols * CELL + PAD, len(names) * CELL + 2 * PAD
    sheet = _gradient(w, h)
    scale = _scale(list(rows.values()))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for r, name in enumerate(names):
        f, size = _label_font(name)
        gd.text((PAD, PAD + r * CELL + CELL // 2 - size // 2 - 6), name, font=f, fill=(255, 150, 30, 255), stroke_width=5, stroke_fill=(255, 150, 30, 255))
    sheet.alpha_composite(glow.filter(ImageFilter.GaussianBlur(7)))
    d = ImageDraw.Draw(sheet)
    for r, name in enumerate(names):
        y0 = PAD + r * CELL
        f, size = _label_font(name)
        d.text((PAD, y0 + CELL // 2 - size // 2 - 6), name, font=f, fill=(92, 14, 14, 255), stroke_width=2, stroke_fill=(60, 8, 8, 255))
        for c, frame in enumerate(rows[name]):
            _place(sheet, frame, scale, LABEL_W + c * CELL, y0)
    buf = io.BytesIO()
    sheet.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def clean_sheet(rows: Dict[str, List[Image.Image]]) -> bytes:
    """The same grid with no labels and a transparent background: one animation per row, ready for a game engine."""
    names = list(rows)
    cols = max(len(r) for r in rows.values())
    sheet = Image.new("RGBA", (cols * CELL, len(names) * CELL), (0, 0, 0, 0))
    scale = _scale(list(rows.values()))
    for r, name in enumerate(names):
        for c, frame in enumerate(rows[name]):
            _place(sheet, frame, scale, c * CELL, r * CELL)
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def build(strips: Dict[str, bytes], frames: int) -> Tuple[bytes, bytes]:
    """strips: {animation name: the image model's strip}. -> (labelled sheet PNG, clean transparent sheet PNG)."""
    rows = {name: split_strip(key_out_background(data), frames) for name, data in strips.items()}
    return labelled_sheet(rows), clean_sheet(rows)
