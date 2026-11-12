"""Server-side edits for saved media. An edit never changes the original: it returns new bytes that the caller saves as a NEW item.

Images (Pillow): crop, resize, brightness, contrast, erase (brush strokes made transparent), remove_background. Transparency is kept by every edit.  Sound (ffmpeg): trim, fade_in, fade_out, volume.
Video: not supported (no provider qualified, see MEDIA-PROVIDERS.md).

ffmpeg is the static binary bundled in the `imageio-ffmpeg` wheel (or an `ffmpeg` on PATH). It is run with a fixed argument list built from
validated numbers only (no shell), reading and writing through pipes, so nothing is written to disk, with a time limit.
"""
from __future__ import annotations

import asyncio
import io
import re
import shutil
import subprocess
from typing import Any, Dict, Optional, Tuple

IMAGE_OPS = ("crop", "resize", "brightness", "contrast", "erase", "remove_background")
ALPHA_OPS = ("erase", "remove_background")          # these make pixels transparent, so the result is always a PNG
SOUND_OPS = ("trim", "fade_in", "fade_out", "volume")
OPERATIONS = {"image": IMAGE_OPS, "sound": SOUND_OPS, "video": ()}

MAX_SIDE = 8192
MAX_BG_PIXELS = 20_000_000                      # background removal holds a few copies of the picture in memory
MAX_STROKES, MAX_STROKE_POINTS = 400, 50_000
MIN_SIDE = 8
MAX_AUDIO_BYTES = 10 * 1024 * 1024
FFMPEG_TIMEOUT_S = 60
MAX_FADE_S = 30.0


class EditError(Exception):
    """A request the edit cannot do (bad numbers, unknown operation). The message is safe to show."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message, self.status_code = message, status_code


def _num(params: Dict[str, Any], name: str, lo: float, hi: float, default: Optional[float] = None, required: bool = False) -> Optional[float]:
    v = params.get(name, default)
    if v is None:
        if required:
            raise EditError(f"'{name}' is required")
        return None
    if isinstance(v, bool):
        raise EditError(f"'{name}' must be a number")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise EditError(f"'{name}' must be a number") from None
    if f != f or f in (float("inf"), float("-inf")) or f < lo or f > hi:
        raise EditError(f"'{name}' must be between {lo:g} and {hi:g}")
    return f


# ── images ──────────────────────────────────────────────────────────────────────────────────────

def _has_alpha(im) -> bool:
    return im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)


def _dilate(mask, size: int):
    import numpy as np
    from PIL import Image, ImageFilter

    return np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(size))) > 127


def _spread(cand, reach, axis: int):
    """Grow `reach` along every run of candidate pixels (along rows for axis 1, along columns for axis 0) that already holds a reached pixel."""
    import numpy as np

    a, r = (cand, reach) if axis == 1 else (cand.T, reach.T)
    flat = a.reshape(-1)
    start = a & ~np.pad(a, ((0, 0), (1, 0)))[:, :-1]
    ids = np.cumsum(start.reshape(-1), dtype=np.int32) * flat                   # 0 for non-candidates, else the number of the run
    hit = np.zeros(int(ids.max()) + 1, bool)
    hit[ids[(r & a).reshape(-1)]] = True
    hit[0] = False
    out = (hit[ids] & flat).reshape(a.shape)
    return out if axis == 1 else out.T


def _reach_from_border(cand):
    """The candidate pixels connected to the picture's edge (row and column sweeps until nothing more is reached)."""
    import numpy as np

    reach = np.zeros_like(cand)
    reach[0, :], reach[-1, :], reach[:, 0], reach[:, -1] = cand[0, :], cand[-1, :], cand[:, 0], cand[:, -1]
    for _ in range(80):
        before = int(reach.sum())
        reach = _spread(cand, _spread(cand, reach, 1), 0)
        if int(reach.sum()) == before:
            break
    return reach


def _remove_background(im, params: Dict[str, Any]):
    """RGBA with the flat background made transparent. The background colour is read from the picture's edge; tolerance is how far a pixel's colour may
    be from it (0 to 100). By default only background connected to the edge goes, so same-coloured details inside the figure (white eyes) stay;
    inside=true removes every pixel of that colour. Memory matters here (a 4K sheet is 17 million pixels): the colour distance is worked out in bands."""
    import numpy as np
    from PIL import Image

    if im.width * im.height > MAX_BG_PIXELS:
        raise EditError("That picture is too large for background removal. Crop it or make it smaller first.", 413)
    tol = _num(params, "tolerance", 0, 100, default=20.0)
    thr = tol * 2.5
    rgb = np.asarray(im if im.mode == "RGB" else im.convert("RGB"))
    base_alpha = np.asarray(im.getchannel("A")) if im.mode == "RGBA" else None          # a picture that already has transparency keeps it
    h, w = rgb.shape[:2]
    ring = np.concatenate([rgb[:3].reshape(-1, 3), rgb[-3:].reshape(-1, 3), rgb[:, :3].reshape(-1, 3), rgb[:, -3:].reshape(-1, 3)])
    bg = np.median(ring, axis=0).astype(np.int32)
    cand = np.empty((h, w), bool)                                                         # pixels close enough to the background colour
    limit = int(thr * thr)
    for y in range(0, h, 256):
        band = rgb[y:y + 256].astype(np.int32) - bg
        cand[y:y + 256] = (band * band).sum(axis=2) <= limit
    gone = cand if params.get("inside") else _reach_from_border(cand)
    alpha = np.full((h, w), 255, np.uint8) if base_alpha is None else np.array(base_alpha)
    alpha[gone] = 0
    edge = _dilate(gone, 5) & ~gone                                                       # a soft edge: pixels just inside the cut are partly clear
    if edge.any() and thr > 0:
        rows, cols = np.nonzero(edge)
        px = rgb[rows, cols].astype(np.float32)
        dist = np.sqrt(((px - bg) ** 2).sum(axis=1))
        a = np.clip((dist - thr * 0.5) / (thr + 1.0), 0, 1)
        alpha[rows, cols] = np.minimum(alpha[rows, cols], (a * 255).astype(np.uint8))
        part = a < 1
        fixed = np.clip((px[part] - bg * (1 - a[part, None])) / np.maximum(a[part, None], 0.25), 0, 255).astype(np.uint8)   # no halo of the old colour
        rgb = np.array(rgb)                                                               # a writable copy (the decoded picture is read-only)
        rgb[rows[part], cols[part]] = fixed
    out = Image.fromarray(rgb)
    out.putalpha(Image.fromarray(alpha))
    return out


def _strokes(params: Dict[str, Any]):
    raw = params.get("strokes")
    if not isinstance(raw, list) or not raw:
        raise EditError("Paint over the parts you want to erase first.")
    if len(raw) > MAX_STROKES:
        raise EditError("That is too many brush strokes at once. Apply the erase, then carry on.")
    out, total = [], 0
    for s in raw:
        if not isinstance(s, list) or len(s) < 3 or len(s) % 2 == 0:
            raise EditError("A brush stroke was not valid.")
        nums = []
        for v in s:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 100000:
                raise EditError("A brush stroke was not valid.")
            nums.append(float(v))
        pts = list(zip(nums[1::2], nums[2::2]))
        total += len(pts)
        if total > MAX_STROKE_POINTS:
            raise EditError("That is too many brush strokes at once. Apply the erase, then carry on.")
        out.append((min(600.0, max(0.5, nums[0])), pts))
    return out


def _erase(im, params: Dict[str, Any]):
    """RGBA with the painted strokes made transparent. A stroke is [radius, x1, y1, x2, y2, ...] in the picture's own pixels."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    mask = Image.new("L", im.size, 0)
    d = ImageDraw.Draw(mask)
    for r, pts in _strokes(params):
        if len(pts) > 1:
            d.line(pts, fill=255, width=max(1, round(2 * r)), joint="curve")
        for x, y in pts:
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    m = np.asarray(mask.filter(ImageFilter.GaussianBlur(0.7)), dtype=np.float32) / 255.0
    rgba = np.array(im.convert("RGBA"))
    rgba[..., 3] = (rgba[..., 3].astype(np.float32) * (1.0 - m)).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def apply_image(data: bytes, mime: str, operation: str, params: Dict[str, Any]) -> Tuple[bytes, str]:
    if operation not in IMAGE_OPS:
        raise EditError(f"Unknown image edit '{operation}'. Use one of: {', '.join(IMAGE_OPS)}.")
    from PIL import Image, ImageEnhance

    Image.MAX_IMAGE_PIXELS = 50_000_000
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise EditError("That image could not be opened.", 422) from None
    keep_alpha = _has_alpha(im) or operation == "erase"                        # transparency survives every edit; the result is then a PNG
    im = im.convert("RGBA" if keep_alpha else "RGB")
    w, h = im.size
    if operation == "crop":
        box = [_num(params, k, 0, MAX_SIDE, required=True) for k in ("left", "top", "right", "bottom")]
        left, top, right, bottom = (int(x) for x in box)
        if right > w or bottom > h or right - left < MIN_SIDE or bottom - top < MIN_SIDE:
            raise EditError(f"The crop box must stay inside the {w}x{h} image and be at least {MIN_SIDE} pixels each way.")
        im = im.crop((left, top, right, bottom))
    elif operation == "resize":
        nw, nh = _num(params, "width", MIN_SIDE, MAX_SIDE), _num(params, "height", MIN_SIDE, MAX_SIDE)
        if nw is None and nh is None:
            raise EditError("Give a width, a height, or both.")
        if nw is None:
            nw = max(MIN_SIDE, round(w * nh / h))
        if nh is None:
            nh = max(MIN_SIDE, round(h * nw / w))
        im = im.resize((int(nw), int(nh)), Image.LANCZOS)
    elif operation == "erase":
        im = _erase(im, params)
    elif operation == "remove_background":
        im = _remove_background(im, params)
    else:
        factor = _num(params, "factor", 0.1, 3.0, required=True)
        enh = ImageEnhance.Brightness if operation == "brightness" else ImageEnhance.Contrast
        if im.mode == "RGBA":                                                   # change the colour only, never the transparency
            r, g, b, a = im.split()
            im = Image.merge("RGBA", (*enh(Image.merge("RGB", (r, g, b))).enhance(factor).split(), a))
        else:
            im = enh(im).enhance(factor)
    out = io.BytesIO()
    if im.mode == "RGBA" or (mime or "").lower() == "image/png":
        im.save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png"
    im.convert("RGB").save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue(), "image/jpeg"


# ── sound ───────────────────────────────────────────────────────────────────────────────────────

def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise EditError("Audio editing is not available on this server.", 503) from None
        return found


def _run_ffmpeg(args: list, data: bytes) -> subprocess.CompletedProcess:
    if len(data) > MAX_AUDIO_BYTES:
        raise EditError("That audio file is too large to edit.", 413)
    try:
        return subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "info", *args], input=data, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise EditError("The audio edit took too long.", 504) from None
    except OSError:
        raise EditError("Audio editing is not available on this server.", 503) from None


_TIME = re.compile(r"time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def probe_duration(data: bytes) -> Optional[float]:
    """Length in seconds, from decoding the audio once (works on piped MP3, which has no reliable header length)."""
    try:
        p = _run_ffmpeg(["-i", "pipe:0", "-f", "null", "-"], data)
    except EditError:
        return None
    times = _TIME.findall(p.stderr.decode("utf-8", "replace"))
    if not times:
        return None
    h, m, s = times[-1]
    return round(int(h) * 3600 + int(m) * 60 + float(s), 2)


def _fmt(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def apply_sound(data: bytes, operation: str, params: Dict[str, Any]) -> bytes:
    if operation not in SOUND_OPS:
        raise EditError(f"Unknown sound edit '{operation}'. Use one of: {', '.join(SOUND_OPS)}.")
    duration = probe_duration(data)
    if duration is None:
        raise EditError("That audio could not be read.", 422)
    if operation == "trim":
        start = _num(params, "start", 0, 36000, default=0.0)
        end = _num(params, "end", 0, 36000)
        if end is None:
            end = duration
        if end > duration:
            end = duration
        if start >= end or end - start < 0.1:
            raise EditError(f"The trim must keep at least 0.1 seconds (this sound is {duration:g} seconds long).")
        flt = f"atrim=start={_fmt(start)}:end={_fmt(end)},asetpts=PTS-STARTPTS"
    elif operation == "fade_in":
        d = _num(params, "seconds", 0.05, MAX_FADE_S, required=True)
        flt = f"afade=t=in:st=0:d={_fmt(min(d, duration))}"
    elif operation == "fade_out":
        d = min(_num(params, "seconds", 0.05, MAX_FADE_S, required=True), duration)
        flt = f"afade=t=out:st={_fmt(max(0.0, duration - d))}:d={_fmt(d)}"
    else:
        flt = f"volume={_fmt(_num(params, 'factor', 0.0, 4.0, required=True))}"
    p = _run_ffmpeg(["-i", "pipe:0", "-af", flt, "-c:a", "libmp3lame", "-q:a", "4", "-f", "mp3", "pipe:1"], data)
    if p.returncode != 0 or not p.stdout:
        raise EditError("The audio edit failed.", 422)
    return p.stdout


async def apply_edit(kind: str, mime: str, data: bytes, operation: str, params: Optional[Dict[str, Any]]) -> Tuple[bytes, str, Optional[float]]:
    """-> (new bytes, new mime, duration for sounds). CPU work runs in a worker thread so the server stays responsive."""
    params = params or {}
    if kind == "image":
        out, m = await asyncio.to_thread(apply_image, data, mime, operation, params)
        return out, m, None
    if kind == "sound":
        out = await asyncio.to_thread(apply_sound, data, operation, params)
        return out, "audio/mpeg", await asyncio.to_thread(probe_duration, out)
    raise EditError("Video cannot be edited: video is not supported.", 400)
