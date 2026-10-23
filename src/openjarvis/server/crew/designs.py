"""Etsy design packs: one artwork made into the files a buyer needs.

make_versions(bytes) -> color PNG and JPG (for printing, sublimation, Canva, Word), black-and-white PNG and JPG, and the
SVG and DXF cut files traced from the black-and-white version. pack_zip() bundles them into one download, in color/ and bw/
folders with a README, so a listing needs only one file.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any, Dict

from PIL import Image

from openjarvis.server.crew import vector

LICENSE_TEXT = (
    "Thank you for buying!\n\n"
    "color/   full-color artwork as PNG and JPG on a white background: printing, sublimation,\n"
    "         Canva, Word and Photoshop.\n"
    "bw/      black-and-white artwork as PNG and JPG, plus cut files: SVG and DXF for Cricut Design Space,\n"
    "         Silhouette Studio and laser cutters. Import the SVG or DXF and set the size in your software.\n\n"
    "License: for personal and small-business use. You may make and sell items you create with these files,\n"
    "but you may not resell, share or redistribute the files themselves.\n"
)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _jpg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


def make_versions(data: bytes) -> Dict[str, Any]:
    """Everything a design pack needs, from one artwork (any PNG, JPG or WEBP)."""
    src = Image.open(io.BytesIO(data))
    src.load()
    if max(src.size) > vector.MAX_SIDE:
        src.thumbnail((vector.MAX_SIDE, vector.MAX_SIDE), Image.LANCZOS)
    color = Image.alpha_composite(Image.new("RGBA", src.size, (255, 255, 255, 255)), src.convert("RGBA")).convert("RGB")
    bw = vector.prepare_bw(color)
    outlines, w, h = vector.trace(bw)
    if not outlines:
        raise ValueError("No clear outlines found: the artwork needs a solid dark design on a plain background.")
    return {
        "color_png": _png(color), "color_jpg": _jpg(color),
        "bw_png": _png(bw), "bw_jpg": _jpg(bw),
        "svg": vector.to_svg(outlines, w, h), "dxf": vector.to_dxf(outlines, w, h),
        "shapes": len(outlines), "width": w, "height": h,
    }


def pack_zip(files: Dict[str, bytes]) -> bytes:
    """files: {"color/name.png": bytes, ...}. Returns the zip with the README added."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", LICENSE_TEXT)
        for path, data in files.items():
            z.writestr(path, data)
    return buf.getvalue()
