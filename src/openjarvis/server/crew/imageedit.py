"""Touch-ups to an existing picture, for the crew: find the lettering in it, then add text or wipe a patch.

inspect(data, y0, y1) reports the lines and words of lettering it finds in a band of the picture, as fractions of its width and height
(0,0 is the top left), so an agent can put a mark exactly where it belongs, for example a colon in the gap after the first word of a title.
edit(data, ops) applies the touch-ups and returns a PNG. Nothing here changes the original; the caller saves the result as a new item.
"""

from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

MAX_SIDE = 4000
MAX_OPS = 12
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf")


def _open(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError("That is not a picture I can open.")
    if max(img.size) > MAX_SIDE:
        raise ValueError("That picture is too large to edit.")
    return img.convert("RGB")


def _frac(v: Any, default: float = 0.0) -> float:
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return default


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(x) for x in c)


def _color(value: Any) -> Optional[Tuple[int, int, int]]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(value or "").strip())
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else None


def _ink(arr: np.ndarray) -> np.ndarray:
    """Lettering is the brightest, most saturated part of a band: a mask of the pixels well above the band's background."""
    lum = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
    lo, hi = float(np.percentile(lum, 20)), float(np.percentile(lum, 99.5))
    if hi - lo < 40:
        return np.zeros(lum.shape, bool)
    return lum > lo + (hi - lo) * 0.55


def _runs(flags: np.ndarray, join: int = 0) -> List[Tuple[int, int]]:
    out: List[List[int]] = []
    start = None
    for i, f in enumerate(list(flags) + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if out and start - out[-1][1] <= join:
                out[-1][1] = i
            else:
                out.append([start, i])
            start = None
    return [(a, b) for a, b in out]


def inspect(data: bytes, y0: float = 0.0, y1: float = 1.0) -> str:
    img = _open(data)
    w, h = img.size
    y0, y1 = _frac(y0), _frac(y1, 1.0)
    if y1 - y0 < 0.02:
        y0, y1 = 0.0, 1.0
    top, bot = int(y0 * h), int(y1 * h)
    arr = np.asarray(img.crop((0, top, w, bot)), dtype=float)
    mask = _ink(arr)
    rows = mask.sum(axis=1) >= max(3, w * 0.004)
    lines = [(a, b) for a, b in _runs(rows, join=max(1, int(h * 0.004))) if b - a >= max(6, h * 0.012)]
    if not lines:
        return f"No lettering found between y={y0:.2f} and y={y1:.2f}. Try a different band."
    out = [f"Picture {w}x{h}. Lettering found between y={y0:.2f} and y={y1:.2f} (all numbers are fractions of the width or height; 0,0 is the top left):"]
    for n, (a, b) in enumerate(lines[:12], 1):
        lh = b - a
        cols = mask[a:b].sum(axis=0) > 0
        glyphs = _runs(cols)
        words: List[Tuple[int, int]] = []
        for g in glyphs:
            if words and g[0] - words[-1][1] < lh * 0.55:
                words[-1] = (words[-1][0], g[1])
            else:
                words.append(g)
        sub = arr[a:b][mask[a:b]]
        color = _hex(np.median(sub, axis=0)) if len(sub) else "unknown"
        ws = " ".join(f"[{x0 / w:.3f}-{x1 / w:.3f}]" for x0, x1 in words[:14])
        gaps = " ".join(f"{(words[i][1] + words[i + 1][0]) / 2 / w:.3f}" for i in range(min(len(words) - 1, 13)))
        out.append(f"Line {n}: y {(top + a) / h:.3f}-{(top + b) / h:.3f} (centre {(top + (a + b) / 2) / h:.3f}, height {lh / h:.3f}), colour {color}. "
                   f"The line spans x {words[0][0] / w:.3f}-{words[-1][1] / w:.3f}. Marks or letters (x ranges): {ws}." + (f" Centres of the gaps between words: {gaps}." if gaps else ""))
    return "\n".join(out)


def _font(px: int) -> ImageFont.ImageFont:
    for f in FONTS:
        try:
            return ImageFont.truetype(f, px)
        except OSError:
            continue
    return ImageFont.load_default(px)


def _sample_ink(img: Image.Image, cx: int, cy: int, half_w: int, half_h: int) -> Tuple[int, int, int]:
    box = (max(0, cx - half_w), max(0, cy - half_h), min(img.width, cx + half_w), min(img.height, cy + half_h))
    arr = np.asarray(img.crop(box), dtype=float)
    mask = _ink(arr)
    return tuple(int(v) for v in (np.median(arr[mask], axis=0) if mask.any() else (255, 255, 255)))


def _erase(img: Image.Image, x0: float, y0: float, x1: float, y1: float) -> None:
    w, h = img.size
    a, b, c, d = int(x0 * w), int(y0 * h), max(int(x0 * w) + 2, int(x1 * w)), max(int(y0 * h) + 2, int(y1 * h))
    arr = np.asarray(img, dtype=float)
    pad = max(2, int((c - a) * 0.03))
    left = np.median(arr[b:d, max(0, a - pad - 4):max(1, a - pad)], axis=1) if a - pad > 0 else None
    right = np.median(arr[b:d, min(w - 1, c + pad):min(w, c + pad + 4)], axis=1) if c + pad < w else None
    if left is None and right is None:
        left = right = np.median(arr[b:d], axis=(0, 1))[None, :].repeat(d - b, 0)
    left = right if left is None else left
    right = left if right is None else right
    t = np.linspace(0, 1, c - a)[None, :, None]
    fill = left[:, None, :] * (1 - t) + right[:, None, :] * t                  # smooth blend of what is on either side, row by row
    arr[b:d, a:c] = fill
    img.paste(Image.fromarray(arr.clip(0, 255).astype("uint8")))


def _text(img: Image.Image, op: Dict[str, Any], orig: Image.Image) -> None:
    w, h = img.size
    text = str(op.get("text") or "")[:80]
    if not text:
        raise ValueError("a text edit needs the text")
    cx, cy = _frac(op.get("x"), 0.5) * w, _frac(op.get("y"), 0.5) * h
    cap = max(4.0, _frac(op.get("size"), 0.04) * h)                            # capital-letter height as a fraction of the picture's height
    font = _font(max(6, int(cap / 0.72)))
    track = float(op.get("tracking") or 0) * cap
    color = _color(op.get("color")) or _sample_ink(orig, int(cx), int(cy), int(w * 0.25), int(cap * 1.6))
    glyph_w = [font.getlength(ch) for ch in text]
    total = sum(glyph_w) + track * (len(text) - 1)
    x = cx - total / 2
    d = ImageDraw.Draw(img)
    ref = d.textbbox((0, 0), "H", font=font)
    base_y = cy - (ref[1] + ref[3]) / 2                                        # centre the capital height on y
    for ch, gw in zip(text, glyph_w):
        d.text((x, base_y), ch, font=font, fill=color)
        x += gw + track


def edit(data: bytes, ops: Any) -> bytes:
    if not isinstance(ops, list) or not ops:
        raise ValueError("give a list of edits")
    img = _open(data)
    orig = img.copy()                                                          # colours are matched from the untouched picture, even after an erase
    for op in ops[:MAX_OPS]:
        if not isinstance(op, dict):
            raise ValueError("each edit is an object")
        kind = str(op.get("op") or "").lower()
        if kind == "text":
            _text(img, op, orig)
        elif kind == "erase":
            _erase(img, _frac(op.get("x0")), _frac(op.get("y0")), _frac(op.get("x1")), _frac(op.get("y1")))
        else:
            raise ValueError('an edit is {"op": "text", ...} or {"op": "erase", ...}')
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def summary(ops: Any) -> str:
    parts = []
    for op in (ops or [])[:MAX_OPS]:
        if isinstance(op, dict):
            parts.append(f'text "{op.get("text")}" at ({op.get("x")}, {op.get("y")})' if str(op.get("op")).lower() == "text" else "erased a patch")
    return "; ".join(parts)
