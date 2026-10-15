"""Server-side edits for saved media. An edit never changes the original: it returns new bytes that the caller saves as a NEW item.

Images (Pillow): crop, resize, brightness, contrast.  Sound (ffmpeg): trim, fade_in, fade_out, volume.
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

IMAGE_OPS = ("crop", "resize", "brightness", "contrast")
SOUND_OPS = ("trim", "fade_in", "fade_out", "volume")
OPERATIONS = {"image": IMAGE_OPS, "sound": SOUND_OPS, "video": ()}

MAX_SIDE = 4096
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
    im = im.convert("RGB")
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
    else:
        factor = _num(params, "factor", 0.1, 3.0, required=True)
        im = (ImageEnhance.Brightness if operation == "brightness" else ImageEnhance.Contrast)(im).enhance(factor)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90, optimize=True)
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
