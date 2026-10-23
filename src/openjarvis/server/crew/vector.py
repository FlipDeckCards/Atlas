"""Vector cut files from black-and-white artwork: SVG and DXF for Cricut, Silhouette and laser cutters.

trace() follows the exact outline of every ink area, simplifies it and fits smooth curves, so the cut lines are clean
curves instead of pixel stairs. SVG keeps the curves; DXF is written as closed polylines sampled from the same curves
(cutting software reads polylines most reliably). Pure Python on top of Pillow: no external tools.
"""

from __future__ import annotations

import math
from typing import List, Tuple

from PIL import Image, ImageFilter

Pt = Tuple[float, float]
INK_LEVEL = 128          # grey below this is ink
MAX_SIDE = 1024          # artwork is traced at this size at most; keeps the work quick and the files a sensible size


def prepare_bw(img: Image.Image, threshold: int = INK_LEVEL) -> Image.Image:
    """Artwork -> black ink on white (mode L). Transparent areas count as white background."""
    rgba = img.convert("RGBA")
    flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba).convert("L")
    flat = flat.filter(ImageFilter.MedianFilter(3))          # knocks out single-pixel speckle before tracing
    return flat.point(lambda v: 0 if v < threshold else 255, mode="L")


def _boundary_edges(data: bytes, w: int, h: int) -> dict:
    """Directed unit edges on the pixel grid, each with the ink on its right-hand side."""
    out: dict = {}

    def ink(x: int, y: int) -> bool:
        return 0 <= x < w and 0 <= y < h and data[y * w + x] > 0

    def add(a, b):
        out.setdefault(a, []).append(b)

    for y in range(h):
        for x in range(w):
            if not ink(x, y):
                continue
            if not ink(x, y - 1):
                add((x, y), (x + 1, y))
            if not ink(x + 1, y):
                add((x + 1, y), (x + 1, y + 1))
            if not ink(x, y + 1):
                add((x + 1, y + 1), (x, y + 1))
            if not ink(x - 1, y):
                add((x, y + 1), (x, y))
    return out


def _loops(out: dict) -> List[List[Pt]]:
    loops = []
    while out:
        start = next(iter(out))
        path, cur = [start], start
        while True:
            nxt = out[cur]
            nb = nxt.pop()
            if not nxt:
                del out[cur]
            cur = nb
            if cur == start:
                break
            path.append(cur)
        loops.append(path)
    return loops


def _area(pts: List[Pt]) -> float:
    return abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))) / 2


def _dp_open(points: List[Pt], eps: float) -> List[Pt]:
    """Douglas-Peucker on an open run of points (iterative, so long outlines cannot hit the recursion limit)."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a], points[b]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1e-9
        best, idx = -1.0, a
        for i in range(a + 1, b):
            px, py = points[i]
            d = abs(dy * (px - x1) - dx * (py - y1)) / length
            if d > best:
                best, idx = d, i
        if best > eps:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k in zip(points, keep) if k]


def _simplify_closed(loop: List[Pt], eps: float) -> List[Pt]:
    """Simplify a closed outline: split it at its two most distant points and simplify each half."""
    p0 = loop[0]
    far = max(range(len(loop)), key=lambda i: (loop[i][0] - p0[0]) ** 2 + (loop[i][1] - p0[1]) ** 2)
    first = _dp_open(loop[: far + 1], eps)
    second = _dp_open(loop[far:] + [p0], eps)
    pts = first[:-1] + second[:-1]
    return pts if len(pts) >= 3 else []


def trace(bw: Image.Image, eps: float = 0.6, min_area: float = 6.0) -> Tuple[List[List[Pt]], int, int]:
    """Closed outlines of every ink area (outer edges and holes). Returns (outlines, width, height)."""
    bw = bw.convert("L")
    w, h = bw.size
    data = bw.point(lambda v: 1 if v < INK_LEVEL else 0).tobytes()
    outlines = []
    for loop in _loops(_boundary_edges(data, w, h)):
        if len(loop) < 4 or _area(loop) < min_area:
            continue                                    # specks and stray pixels are not cut
        pts = _simplify_closed(loop, eps)
        if len(pts) >= 3:
            outlines.append(pts)
    return outlines, w, h


def _unit(vx: float, vy: float) -> Pt:
    n = math.hypot(vx, vy) or 1e-9
    return vx / n, vy / n


def _segments(pts: List[Pt]):
    """Closed smooth curve through the points, as cubic Bezier segments (p1, c1, c2, p2).
    Tangents follow the neighbouring points but are capped to a third of each segment, so straight edges stay straight
    and corners round off only slightly."""
    n = len(pts)
    for i in range(n):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        reach = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / 3
        ux, uy = _unit(p2[0] - p0[0], p2[1] - p0[1])
        vx, vy = _unit(p3[0] - p1[0], p3[1] - p1[1])
        c1 = (p1[0] + ux * reach, p1[1] + uy * reach)
        c2 = (p2[0] - vx * reach, p2[1] - vy * reach)
        yield p1, c1, c2, p2


def _f(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") or "0"


def to_svg(outlines: List[List[Pt]], w: int, h: int, fill: str = "#000000") -> str:
    d = []
    for pts in outlines:
        d.append(f"M{_f(pts[0][0])},{_f(pts[0][1])}")
        for _, c1, c2, p in _segments(pts):
            d.append(f"C{_f(c1[0])},{_f(c1[1])} {_f(c2[0])},{_f(c2[1])} {_f(p[0])},{_f(p[1])}")
        d.append("Z")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<path fill="{fill}" fill-rule="evenodd" d="{" ".join(d)}"/></svg>\n')


def _sample(pts: List[Pt], steps: int = 6) -> List[Pt]:
    out: List[Pt] = []
    for p0, c1, c2, p3 in _segments(pts):
        for k in range(steps):
            t = k / steps
            u = 1 - t
            x = u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p3[0]
            y = u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p3[1]
            out.append((x, y))
    return out


def to_dxf(outlines: List[List[Pt]], w: int, h: int) -> str:
    """ASCII DXF: one closed polyline per outline on layer CUT, units as pixels (scale the artwork in the cutting software)."""
    lines = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$INSUNITS", "70", "0", "0", "ENDSEC",
             "0", "SECTION", "2", "ENTITIES"]
    for pts in outlines:
        poly = _sample(pts)
        lines += ["0", "LWPOLYLINE", "8", "CUT", "90", str(len(poly)), "70", "1"]
        for x, y in poly:
            lines += ["10", f"{x:.3f}", "20", f"{h - y:.3f}"]
    lines += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(lines) + "\n"
