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


def back_cover_ops(w: float, h: float, m: float, tagline: str, blurb: str, author: str, barcode_space: bool) -> str:
    """Page-content operators that typeset a back cover over the picture: a dark panel for legibility, the tagline, the blurb, the author, and a white
    box where the printer's barcode goes. Real text in a real font: image generators cannot spell, so the words are never part of the picture.
    The type shrinks (and, if it must, the description loses its last paragraphs) so nothing ever runs into the barcode box or off the page."""
    inner = w - 2 * m
    box_h = 72 if barcode_space else 0
    floor = m + box_h + (22 if barcode_space else 0)
    avail = (h - m - 20) - floor
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", blurb.strip()) if p.strip()]
    pitch = lambda size: size * 1.38

    def build(scale: float, ps: List[str]):
        lines: List[Tuple[str, float, bool]] = []
        if tagline.strip():
            lines += [(ln, 18 * scale, True) for ln in _wrap(_clean(tagline.strip()), 18 * scale, inner, True)] + [("", 10 * scale, False)]
        for para in ps:
            lines += [(ln, 11.5 * scale, False) for ln in _wrap(_clean(para), 11.5 * scale, inner)] + [("", 6 * scale, False)]
        if author.strip():
            lines += [("", 8 * scale, False), (_clean(author.strip()), 12 * scale, True)]
        return lines, sum(pitch(sz) for _t, sz, _b in lines)

    lines, need = [], 0.0
    for scale in (1.0, 0.93, 0.86, 0.8, 0.74, 0.68):
        lines, need = build(scale, paras)
        if need <= avail:
            break
    while need > avail and len(paras) > 1:                                    # still too long at the smallest size: the last paragraph goes
        paras = paras[:-1]
        lines, need = build(0.68, paras)
    while need > avail and paras and len(paras[0].split()) > 12:              # one very long paragraph: shorten it at a word
        paras[0] = " ".join(paras[0].split()[:-8]).rstrip(",;:") + "..."
        lines, need = build(0.68, paras)
    top = floor + need
    ops = []
    if lines:
        ops.append(f"q /GS1 gs 0.02 0.04 0.09 rg {m - 14:g} {floor - 16:g} {inner + 28:g} {top - floor + 30:g} re f Q")        # translucent panel
    y = top
    ops.append("BT 1 1 1 rg")
    for text, size, bold in lines:
        y -= pitch(size)
        if text:
            ops.append(f"/{'F2' if bold else 'F1'} {size:g} Tf 1 0 0 1 {m:g} {y:.1f} Tm ({_esc(text)}) Tj")
    ops.append("ET")
    if barcode_space:
        ops.append(f"q 1 1 1 rg {w - m - 144:g} {m:g} 144 {box_h:g} re f Q")                                                     # white box for the printer's barcode
    return "\n".join(ops)


def _build(pages, w: float, h: float, m: float, front=None, back=None, back_text=None) -> bytes:
    """Assemble the PDF. front / back: optional (jpeg, wpx, hpx) shown full-bleed as the first and last page. back_text: optional
    {tagline, blurb, author, barcode_space} typeset over the back cover (or on a plain dark back page when there is no picture)."""
    objs: List[bytes] = []

    def add(b: "str | bytes") -> int:
        objs.append(b if isinstance(b, bytes) else b.encode("latin-1"))
        return len(objs)

    add("<< /Type /Catalog /Pages 2 0 R >>")
    add("")                                                           # pages tree, filled in below
    f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []

    gs = add("<< /Type /ExtGState /ca 0.58 /CA 0.58 >>")

    def image_page(img, overlay: str = "") -> int:
        if img:
            data, wpx, hpx = img
            iid = add(f"<< /Type /XObject /Subtype /Image /Width {wpx} /Height {hpx} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode("latin-1") + data + b"\nendstream")
            base = f"q {w:g} 0 0 {h:g} 0 0 cm /Im0 Do Q\n"
            xo = f"/XObject << /Im0 {iid} 0 R >> "
        else:
            base, xo = f"0.02 0.04 0.09 rg 0 0 {w:g} {h:g} re f\n", ""                          # no picture: a plain dark page
        stream = (base + overlay).encode("latin-1", "replace")
        cid = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        return add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:g} {h:g}] /Resources << {xo}/Font << /F1 {f1} 0 R /F2 {f2} 0 R >> /ExtGState << /GS1 {gs} 0 R >> >> /Contents {cid} 0 R >>")

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
    if back or back_text:
        bt = back_text or {}
        has = bool(str(bt.get("tagline") or "").strip() or str(bt.get("blurb") or "").strip() or str(bt.get("author") or "").strip() or bt.get("barcode_space"))
        kids.append(image_page(back, back_cover_ops(w, h, m, str(bt.get("tagline") or ""), str(bt.get("blurb") or ""), str(bt.get("author") or ""), bool(bt.get("barcode_space"))) if has else ""))
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


def make_book_pdf(title: str, author: str, body: str, cover: "bytes | None" = None, back_cover: "bytes | None" = None,
                  back_text: "dict | None" = None, subtitle: str = "", publisher: str = "") -> Tuple[bytes, int]:
    """A 6 x 9 inch paperback: front cover, title page, copyright page, a real contents list, the manuscript typeset as a book (see typeset.py), and
    the back cover as the last page, with its words set in real type (back_text {tagline, blurb, author, barcode_space}).
    cover and back_cover are image files of any size or type; they are cropped to the page and compressed. -> (pdf bytes, number of pages)"""
    from openjarvis.server.crew import typeset

    return typeset.make_book(title, author, body, cover, back_cover, back_text, subtitle=subtitle, publisher=publisher)
