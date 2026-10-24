"""A small, dependency-free text-to-PDF maker for the crew's digital products (guides, planners, checklists written in Markdown).

It lays out Markdown-ish text on Letter pages with the standard Helvetica fonts: '#' headings, '-'/'*' bullets, numbered lines,
'---' rules, blank-line paragraphs, word wrap and page breaks, a title page and page numbers. Characters outside Latin-1 are
replaced with close ASCII equivalents, because the standard PDF fonts only cover Latin-1.
"""

from __future__ import annotations

import re
from typing import List, Tuple

W, H, M = 612, 792, 64                       # Letter, 0.9 inch margins
SUBS = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "•": "-",
        " ": " ", "→": "->", "✓": "v", "✔": "v", "☐": "[ ]", "☑": "[x]", "★": "*"}
WIDTHS = {  # Helvetica glyph widths (1/1000 em) for the printable ASCII range; others use 556
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278,
    "-": 333, ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015, "[": 278, "\\": 278, "]": 278,
    "^": 469, "_": 556, "`": 333, "{": 334, "|": 260, "}": 334, "~": 584, "I": 278, "J": 500, "M": 833, "W": 944, "f": 278, "i": 222,
    "j": 222, "l": 222, "m": 833, "r": 333, "t": 278, "w": 722, "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "K": 667, "L": 556, "N": 722, "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "X": 667,
    "Y": 667, "Z": 611, "c": 500, "k": 500, "s": 500, "v": 500, "x": 500, "y": 500, "z": 500}


def _clean(text: str) -> str:
    for a, b in SUBS.items():
        text = text.replace(a, b)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)       # bold markers: plain text
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)
    return text.encode("latin-1", "replace").decode("latin-1")


def _width(s: str, size: float, bold: bool = False) -> float:
    return sum(WIDTHS.get(ch, 556) for ch in s) * size / 1000 * (1.06 if bold else 1.0)


def _wrap(text: str, size: float, width: float, bold: bool = False) -> List[str]:
    out, line = [], ""
    for word in text.split():
        cand = (line + " " + word).strip()
        if _width(cand, size, bold) <= width or not line:
            line = cand
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    return out or [""]


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def layout(title: str, body: str, w: float = W, h: float = H, m: float = M, author: str = "", chapter_breaks: bool = False,
           body_size: float = 11) -> List[List[Tuple[str, float, float, float]]]:
    """-> pages of (text, font size, x, y, ...) runs. Font is encoded in the size sign: negative = bold.
    w, h, m: the page and its margin in points (Letter and 0.9 inch by default). chapter_breaks: every '# ' heading starts a new page (a book)."""
    W, H, M = w, h, m                                    # the page this layout is for (shadows the Letter defaults for this call)
    pages: List[List[Tuple[str, float, float, float]]] = []
    cur: List[Tuple[str, float, float, float]] = []
    y = H - M

    def new_page():
        nonlocal cur, y
        if cur:
            pages.append(cur)
        cur, y = [], H - M

    def put(text: str, size: float, x: float, bold: bool, gap: float):
        nonlocal y
        if y - size < M + 20:
            new_page()
        y -= size
        cur.append((text, -size if bold else size, x, y))
        y -= gap

    # title page
    y = H * 0.62
    for ln in _wrap(_clean(title), 30 if W > 500 else 26, W - 2 * M, True):
        put(ln, 30 if W > 500 else 26, M, True, 10)
    if author:
        y -= 14
        for ln in _wrap(_clean(author), 14, W - 2 * M):
            put(ln, 14, M, False, 6)
    new_page()
    for raw in body.replace("\r", "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            y -= 8
            continue
        if re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", line):
            put("_" * 60, 9, M, False, 8)
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            size = {1: 22, 2: 17, 3: 14, 4: 12}[len(m.group(1))]
            if chapter_breaks and len(m.group(1)) == 1 and cur:
                new_page()
                y -= 40                                  # chapters start a little way down the page
            y -= 8
            for ln in _wrap(_clean(m.group(2)), size, W - 2 * M, True):
                put(ln, size, M, True, 6)
            continue
        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)", line)
        if m:
            indent = M + 14 + min(len(m.group(1)), 8) * 4
            mark = "-" if m.group(2) in "-*+" else m.group(2)
            lines = _wrap(_clean(m.group(3)), body_size, W - M - indent - 12)
            put(mark + " " + lines[0], body_size, indent, False, 4)
            for ln in lines[1:]:
                put(ln, body_size, indent + 12, False, 4)
            continue
        for ln in _wrap(_clean(line.strip()), body_size, W - 2 * M):
            put(ln, body_size, M, False, 5)
    new_page()
    return pages


def prepare_image(data: bytes, w_pt: float, h_pt: float, dpi: int = 220, quality: int = 85) -> Tuple[bytes, int, int]:
    """A picture cropped to the page's shape and shrunk to print resolution as a JPEG, so a heavy original does not bloat the book.
    -> (jpeg bytes, width px, height px)"""
    import io

    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    wpx, hpx = int(w_pt / 72 * dpi), int(h_pt / 72 * dpi)
    img = ImageOps.fit(img, (wpx, hpx), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue(), wpx, hpx


def _build(pages, w: float, h: float, m: float, front=None, back=None) -> bytes:
    """Assemble the PDF. front / back: optional (jpeg, wpx, hpx) shown full-bleed as the first and last page."""
    objs: List[bytes] = []

    def add(b: "str | bytes") -> int:
        objs.append(b if isinstance(b, bytes) else b.encode("latin-1"))
        return len(objs)

    add("<< /Type /Catalog /Pages 2 0 R >>")
    add("")                                                           # pages tree, filled in below
    f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []

    def image_page(img) -> int:
        data, wpx, hpx = img
        iid = add(f"<< /Type /XObject /Subtype /Image /Width {wpx} /Height {hpx} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode("latin-1") + data + b"\nendstream")
        stream = f"q {w:g} 0 0 {h:g} 0 0 cm /Im0 Do Q".encode()
        cid = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        return add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:g} {h:g}] /Resources << /XObject << /Im0 {iid} 0 R >> >> /Contents {cid} 0 R >>")

    if front:
        kids.append(image_page(front))
    for n, page in enumerate(pages, 1):
        ops = ["BT"]
        for text, size, x, y in page:
            ops.append(f"/{'F2' if size < 0 else 'F1'} {abs(size):g} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm ({_esc(text)}) Tj")
        if n > 1:
            ops.append(f"/F1 9 Tf 1 0 0 1 {w / 2 - 8:.1f} {m / 2:.1f} Tm ({n}) Tj")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1", "replace")
        cid = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        kids.append(add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:g} {h:g}] /Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> "
                        f"/Contents {cid} 0 R >>"))
    if back:
        kids.append(image_page(back))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_pdf(title: str, body: str) -> bytes:
    return _build(layout(title, body), W, H, M)


BOOK_W, BOOK_H, BOOK_M = 432, 648, 54                    # 6 x 9 inch trade paperback, 0.75 inch margins


def make_book_pdf(title: str, author: str, body: str, cover: "bytes | None" = None, back_cover: "bytes | None" = None) -> Tuple[bytes, int]:
    """A 6 x 9 inch book: the front cover as page one, a title page, the manuscript (every '# ' chapter on a new page, no pictures inside),
    and the back cover as the last page. cover and back_cover are image files of any size or type; they are cropped to the page and compressed.
    -> (pdf bytes, number of pages)"""
    front = prepare_image(cover, BOOK_W, BOOK_H) if cover else None
    back = prepare_image(back_cover, BOOK_W, BOOK_H) if back_cover else None
    pages = layout(title, body, BOOK_W, BOOK_H, BOOK_M, author=author, chapter_breaks=True, body_size=11)
    return _build(pages, BOOK_W, BOOK_H, BOOK_M, front, back), len(pages) + (1 if front else 0) + (1 if back else 0)
