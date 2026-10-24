"""A book typesetter: a manuscript in Markdown becomes the PDF of a real 6 x 9 inch paperback.

What it does that a document converter does not:
  * serif type (Times) set justified, with the line breaks chosen for whole paragraphs (no loose, gappy lines), first-line indents, and
    widow and orphan control (no lone line stranded at the top or bottom of a page);
  * a title page, a copyright page and a real table of contents with dotted leaders and page numbers, made from the manuscript's own headings;
  * Part pages and chapter openings with centered headings, running heads (book title on left pages, chapter on right pages) and page numbers;
  * *italic* and **bold** typeset as italic and bold, "* * *" scene breaks, block quotes, lists, and short dialogue lines kept as lines;
  * mirrored margins (a wider inner margin where the pages are bound);
  * the manuscript's own title block and contents list are removed (the book makes its own), and notes meant for the crew, such as image
    placeholders and media-library ids, never reach the page;
  * the front cover as page one and the back cover as the last page, full bleed, with the back-cover words set in real type.

Standard PDF fonts only (Times), so the file is small and prints anywhere. Characters are written in Windows-1252 so real curly quotes, dashes
and ellipses survive. Line widths come from typeset_metrics (measured from a Times-compatible face).
"""

from __future__ import annotations

import datetime as dt
import re
import zlib
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.server.crew import pdf as pdfbase
from openjarvis.server.crew.typeset_metrics import WIDTHS

W_PT, H_PT = 432.0, 648.0                       # 6 x 9 inches
INNER, OUTER = 66.0, 46.0                       # margins: where the pages are bound, and the outer edge
TEXT_W = W_PT - INNER - OUTER                   # 320 pt
TOP_Y, BOT_Y = H_PT - 66.0, 66.0                # the text block runs between these baselines (top is the first line's top)
SIZE, LEAD, INDENT = 11.0, 14.6, 16.0
FONT_NO = {"R": 1, "B": 2, "I": 3, "BI": 4}
FONT_NAME = {"R": "Times-Roman", "B": "Times-Bold", "I": "Times-Italic", "BI": "Times-BoldItalic"}

SUBS = {"→": "->", "←": "<-", "✓": "v", "✔": "v", "☐": "[ ]", "☑": "[x]", "★": "*", " ": " ", "​": "", " ": " ", " ": " ",
        "‑": "-", "−": "-", "≈": "~", "≠": "!=", "≥": ">=", "≤": "<=", "×": "x", "…": "…"}

CHAPTERISH = re.compile(r"^(chapter|part|introduction|conclusion|preface|prologue|epilogue|foreword|afterword|acknowledg|about the author|appendix|notes)\b", re.I)
PART = re.compile(r"^part\s+(\w+)\s*[:.\-–—]?\s*(.*)$", re.I)
CHAPTER = re.compile(r"^chapter\s+(\w+)\s*[:.\-–—]?\s*(.*)$", re.I)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
HR = re.compile(r"^\s*(([-*_])\s*){3,}$")


# ── text and measuring ───────────────────────────────────────────────────────────────────────────────────────

def cp(s: str) -> str:
    """Text the Windows-1252 fonts can show: close ASCII for what they cannot."""
    for a, b in SUBS.items():
        s = s.replace(a, b)
    return s.encode("cp1252", "replace").decode("cp1252")


def tw(text: str, style: str, size: float) -> float:
    table = WIDTHS[style]
    return sum(table[c - 32] if 32 <= c < 256 else 500 for c in text.encode("cp1252", "replace")) * size / 1000.0


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def clean_notes(text: str) -> str:
    """Drop what was written for the crew and not for readers: image and graphic placeholders, media-library ids, 'see the diagram' captions."""
    out = []
    for line in text.replace("\r", "").split("\n"):
        low = line.lower()
        if "media library" in low or UUID.search(line) or re.match(r"^\s*!\[", line):
            continue
        if re.match(r"^\s*[\[(]?\s*\**\s*(image|graphic|figure|illustration|diagram)\b[^\n]{0,200}$", low) and len(line) < 220:
            continue
        if re.match(r"^\s*\*?\(?\s*see (the |our )?[^)\n]{0,120}(diagram|graphic|figure|image|illustration|chart)[^)\n]{0,60}\)?\*?\s*$", low):
            continue
        out.append(line)
    return re.sub(r"<!--.*?-->", "", "\n".join(out), flags=re.S)


def split_front(text: str) -> Tuple[Dict[str, str], str]:
    """The manuscript's own title block and contents list (the book makes its own). -> (title, subtitle, author, publisher found there; the rest)."""
    head = text[:8000]
    m = re.search(r"(?im)^\s{0,3}#{1,6}\s*(table of contents|contents)\s*$", head)
    if not m:
        return {}, text
    rest = text[m.end():]
    r = re.search(r"(?m)^\s*([-*_])\1{2,}\s*$", rest)
    if not r:
        return {}, text
    info: Dict[str, str] = {}
    heads, lines = [], [ln.strip() for ln in text[:m.start()].split("\n") if ln.strip() and not HR.match(ln.strip())]
    for i, ln in enumerate(lines):
        mh = re.match(r"^#{1,6}\s+(.*)$", ln)
        if mh:
            heads.append(mh.group(1).strip())
        mb = re.match(r"^[*_]*by\s+(.+?)[*_]*$", ln, re.I)
        if mb and "author" not in info:
            info["author"] = mb.group(1).strip(" *_")
            if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                info["publisher"] = lines[i + 1].strip(" *_")
    if heads:
        info["title"] = heads[0]
    if len(heads) > 1:
        info["subtitle"] = heads[1]
    return info, rest[r.end():].lstrip("\n")


# ── inline styles ────────────────────────────────────────────────────────────────────────────────────────────

INLINE = re.compile(r"(?P<bi>\*\*\*(?=\S)(?P<bi_t>.+?)(?<=\S)\*\*\*)|(?P<b>\*\*(?=\S)(?P<b_t>.+?)(?<=\S)\*\*)|(?P<i>\*(?=[^\s*])(?P<i_t>.+?)(?<=[^\s*])\*)"
                    r"|(?P<u>(?<![\w])_(?=\S)(?P<u_t>[^_]+?)(?<=\S)_(?![\w]))")


def runs(text: str, base: str = "R") -> List[Tuple[str, str]]:
    text = re.sub(r"\[([^\]]+)\]\((?:https?://|mailto:)[^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)

    def combine(extra: str) -> str:
        bold = "B" in base or extra in ("B", "BI")
        ital = "I" in base or extra in ("I", "BI")
        return "BI" if bold and ital else "B" if bold else "I" if ital else "R"
    out, pos = [], 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], base))
        if m.group("bi"):
            out.append((m.group("bi_t"), combine("BI")))
        elif m.group("b"):
            out.append((m.group("b_t"), combine("B")))
        elif m.group("i"):
            out.append((m.group("i_t"), combine("I")))
        else:
            out.append((m.group("u_t"), combine("I")))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], base))
    return [(cp(t), s) for t, s in out if t]


def to_words(rs: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
    words, cur = [], []
    for text, style in rs:
        for part in re.split(r"(\s+)", text):
            if not part:
                continue
            if part.isspace():
                if cur:
                    words.append(cur)
                    cur = []
            else:
                cur.append((part, style))
    if cur:
        words.append(cur)
    return words


def plain(rs: List[Tuple[str, str]]) -> str:
    return "".join(t for t, _s in rs)


# ── breaking a paragraph into lines ──────────────────────────────────────────────────────────────────────────

class Line:
    __slots__ = ("pieces", "x", "width", "tw", "size")

    def __init__(self, pieces, x, width, word_space, size):
        self.pieces, self.x, self.width, self.tw, self.size = pieces, x, width, word_space, size


def break_lines(words: List[List[Tuple[str, str]]], first_w: float, rest_w: float, size: float, first_x: float = 0.0, rest_x: float = 0.0,
                justify: bool = True) -> List[Line]:
    """Choose the line breaks for the whole paragraph at once (least total looseness), then set each line justified except the last."""
    n = len(words)
    if n == 0:
        return []
    wl = [sum(tw(t, s, size) for t, s in w) for w in words]
    sp = tw(" ", "R", size)
    INF = 1e18
    best, nxt = [INF] * (n + 1), [0] * (n + 1)
    best[n] = 0.0
    for i in range(n - 1, -1, -1):
        target = first_w if i == 0 else rest_w
        w = 0.0
        for j in range(i, min(n, i + 60)):
            w += wl[j] + (sp if j > i else 0.0)
            gaps = j - i
            if w > target + (0.14 * sp * gaps if justify and j < n - 1 else 0.0) and j > i:
                break
            slack = target - w
            if j == n - 1:
                cost = 0.0 if slack >= 0 else slack * slack * 4
                if gaps == 0 and wl[j] < 3 * sp and n > 1:
                    cost += 300.0                                  # no lone short word on a last line
            else:
                cost = slack * slack if slack >= 0 else slack * slack * 6
            if best[j + 1] < INF and cost + best[j + 1] < best[i]:
                best[i], nxt[i] = cost + best[j + 1], j + 1
        if best[i] >= INF:                                          # one word wider than the line: it gets a line to itself
            best[i], nxt[i] = best[i + 1] + 1e6 if i + 1 <= n and best[i + 1] < INF else 1e6, i + 1
    lines, i = [], 0
    while i < n:
        j = nxt[i]
        target = first_w if i == 0 else rest_w
        group = words[i:j]
        natural = sum(wl[i:j]) + sp * (len(group) - 1)
        pieces: List[Tuple[str, str]] = []
        for k, w in enumerate(group):
            if k:
                pieces.append((pieces[-1][0] if pieces else "R", " "))
            pieces += [(s, t) for t, s in w]
        merged: List[List[str]] = []
        for s, t in pieces:
            if merged and merged[-1][0] == s:
                merged[-1][1] += t
            else:
                merged.append([s, t])
        last = j >= n
        word_space = (target - natural) / (len(group) - 1) if (justify and not last and len(group) > 1) else 0.0
        lines.append(Line([(s, t) for s, t in merged], first_x if i == 0 else rest_x, natural + word_space * (len(group) - 1), word_space, size))
        i = j
    return lines


# ── pages ────────────────────────────────────────────────────────────────────────────────────────────────────

class Page:
    def __init__(self, kind: str, head: str = "", number: Optional[int] = None) -> None:
        self.kind, self.head, self.number = kind, head, number
        self.ops: List[tuple] = []
        self.y = TOP_Y


def block_split(text: str) -> List[Dict[str, Any]]:
    """Markdown into blocks: headings, paragraphs (or short separate lines), quotes, lists, rules."""
    blocks: List[Dict[str, Any]] = []
    para: List[str] = []
    quote: List[str] = []
    items: List[str] = []

    def flush():
        nonlocal para, quote, items
        if para:
            groups: List[str] = [para[0]]
            for ln in para[1:]:
                if re.search(r"[.!?:”\"')\]*_]$", groups[-1]) and re.match(r"^[A-Z\"“‘'(\[*_0-9]", ln):
                    groups.append(ln)                                        # an intended new line
                else:
                    groups[-1] += " " + ln                                   # a wrapped line of the same paragraph
            if len(groups) >= 2 and all(len(g) <= 170 for g in groups):
                blocks.append({"t": "lines", "items": groups})
            else:
                blocks.extend({"t": "p", "text": g} for g in groups)
        if quote:
            blocks.append({"t": "quote", "text": " ".join(quote)})
        if items:
            blocks.append({"t": "list", "items": items})
        para, quote, items = [], [], []

    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            flush()
            continue
        if HR.match(s):
            flush()
            blocks.append({"t": "hr"})
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", s)
        if m:
            flush()
            blocks.append({"t": "h", "level": len(m.group(1)), "text": m.group(2).strip()})
            continue
        if s.startswith(">"):
            if para or items:
                flush()
            quote.append(s.lstrip("> ").strip())
            continue
        m = re.match(r"^([-*+•]|\d+[.)])\s+(.*)$", s)
        if m:
            if para or quote:
                flush()
            items.append(m.group(2))
            continue
        if items and raw.startswith((" ", "\t")):
            items[-1] += " " + s                                            # an indented continuation of a list item
            continue
        if quote or items:
            flush()
        para.append(s)
    flush()
    return drop_stray_rules(blocks)


def drop_stray_rules(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A rule only means something between two passages of text: not at the start or end, beside a heading, or beside another rule."""
    def opens(b):
        return b["t"] == "h" and (b["level"] == 1 or PART.match(b["text"]) or CHAPTERISH.match(b["text"]))
    out: List[Dict[str, Any]] = []
    for i, b in enumerate(blocks):
        if b["t"] == "hr":
            prev = out[-1] if out else None
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            if prev is None or nxt is None or prev["t"] in ("hr", "h") or nxt["t"] == "hr" or opens(nxt):
                continue
        out.append(b)
    return out


class Book:
    def __init__(self, title: str) -> None:
        self.title = title
        self.pages: List[Page] = []
        self.cur: Optional[Page] = None
        self.toc: List[Tuple[str, str, int]] = []                           # (kind, text, page number)
        self.chapter = ""
        self.after_para = False
        self.pending_open = False                                            # a Part page was just set: the next text opens a new body page

    def ensure(self) -> None:
        if self.cur is None or self.pending_open:
            self.new_page("body")
            self.pending_open = False

    # -- flow -----------------------------------------------------------------------------------------------
    def new_page(self, kind: str = "body", head: Optional[str] = None) -> Page:
        number = len(self.pages) + 1
        p = Page(kind, self.chapter if head is None else head, number)
        self.pages.append(p)
        self.cur = p
        return p

    def room(self) -> float:
        return self.cur.y - BOT_Y

    def put_lines(self, lines: List[Line], lead: float, after: float = 0.0) -> None:
        """Place a paragraph's lines, never leaving one line alone at the bottom of a page (orphan) or at the top of the next (widow)."""
        self.ensure()
        n, i = len(lines), 0
        while i < n:
            fit = int(self.room() // lead)
            left = n - i
            take = left if fit >= left else fit
            if take < left:
                if take < 2:
                    take = 0                                                    # not even two lines fit: start the paragraph on the next page
                elif left - take == 1:
                    take = take - 1 if take >= 3 else 0                         # the last line would be alone on the next page: carry one more over
            if take <= 0:
                self.new_page()
                continue
            for ln in lines[i:i + take]:
                self.cur.y -= lead
                self.cur.ops.append(("line", ln, self.cur.y))
            i += take
            if i < n:
                self.new_page()
        self.cur.y -= after

    def need(self, height: float) -> None:
        self.ensure()
        if self.room() < height:
            self.new_page()

    # -- blocks ---------------------------------------------------------------------------------------------
    def paragraph(self, text: str, indent: bool, base: str = "R", size: float = SIZE, lead: float = LEAD, left: float = 0.0, right: float = 0.0,
                  justify: bool = True, after: float = 0.0) -> None:
        words = to_words(runs(text, base))
        width = TEXT_W - left - right
        lines = break_lines(words, width - (INDENT if indent else 0.0), width, size, left + (INDENT if indent else 0.0), left, justify)
        self.put_lines(lines, lead, after=after)

    def centered(self, text: str, size: float, style: str = "B", gap: float = 0.0, tracking: float = 0.0, lead: Optional[float] = None, width: float = 300.0) -> None:
        lead = lead or size * 1.25
        words = to_words(runs(text, style))
        lines = break_lines(words, width, width, size, justify=False)
        for ln in lines:
            self.cur.y -= lead
            self.cur.ops.append(("center", ln, self.cur.y, tracking))
        self.cur.y -= gap

    def ornament(self, y_gap: float = 10.0) -> None:
        self.cur.y -= y_gap
        self.cur.ops.append(("rule", self.cur.y, 26.0))
        self.cur.y -= y_gap

    def chapter_opening(self, title: str, kind: str) -> None:
        label, name = "", title
        m = CHAPTER.match(title)
        if m:
            label, name = f"CHAPTER {m.group(1).upper()}", m.group(2).strip()
        self.chapter = name or title
        self.new_page("opener", head="")
        self.cur.y = H_PT - 150
        if label:
            self.centered(label, 9.5, "B", gap=6, tracking=2.4)
        self.centered(name or title, 21, "B", gap=2, lead=26)
        self.ornament(8)
        self.cur.y -= 18
        self.toc.append(("chapter" if label else "front", f"Chapter {m.group(1)}: {name}" if label else title, self.cur.number))
        self.after_para = False

    def part_page(self, title: str, subtitle: str = "") -> None:
        m = PART.match(title)
        label, name = (f"PART {m.group(1).upper()}", m.group(2).strip()) if m else ("", title)
        self.new_page("part", head="")
        self.cur.y = H_PT * 0.58
        if label:
            self.centered(label, 11, "B", gap=10, tracking=3.0)
        self.centered(name or title, 26, "B", gap=4, lead=32)
        if subtitle:
            self.centered(subtitle, 14, "I", gap=4, lead=19)
        self.ornament(12)
        self.toc.append(("part", f"Part {m.group(1)}: {name}" + (f" - {subtitle}" if subtitle else "") if m else title, self.cur.number))
        self.after_para = False
        self.chapter = name or title
        self.pending_open = True                                         # whatever comes next starts on its own page

    def subheading(self, text: str, level: int) -> None:
        size = 13.0 if level <= 2 else 11.5
        words = to_words(runs(text, "B"))
        lines = break_lines(words, TEXT_W, TEXT_W, size, justify=False)
        self.need(len(lines) * size * 1.3 + 24 + 2 * LEAD)
        self.cur.y -= 10
        for ln in lines:
            self.cur.y -= size * 1.3
            self.cur.ops.append(("line", ln, self.cur.y))
        self.cur.y -= 3
        self.after_para = False

    def scene_break(self) -> None:
        self.need(LEAD * 3)
        self.cur.y -= LEAD * 0.4
        self.cur.y -= LEAD
        self.cur.ops.append(("stars", self.cur.y))
        self.cur.y -= LEAD * 0.3
        self.after_para = False

    def run(self, blocks: List[Dict[str, Any]]) -> None:
        i = 0
        while i < len(blocks):
            b = blocks[i]
            t = b["t"]
            if t == "h":
                text, level = b["text"], b["level"]
                if PART.match(text) and level <= 2:
                    sub = ""
                    if i + 1 < len(blocks) and blocks[i + 1]["t"] == "h" and not CHAPTERISH.match(blocks[i + 1]["text"]):
                        sub = blocks[i + 1]["text"]
                        i += 1
                    self.part_page(text, sub)
                elif level == 1 or (level == 2 and CHAPTERISH.match(text)):
                    self.chapter_opening(text, "chapter")
                    self.pending_open = False
                else:
                    self.subheading(text, level)
            elif t == "p":
                self.ensure()
                if b["text"].isupper() and len(b["text"]) <= 30:                  # a short label in capitals (DEFENSE): a small bold heading
                    self.subheading(b["text"].title(), 3)
                else:
                    self.paragraph(b["text"], indent=self.after_para)
                    self.after_para = True
            elif t == "lines":
                self.ensure()
                self.cur.y -= 3
                for k, ln in enumerate(b["items"]):
                    self.paragraph(ln, indent=False, justify=False, after=2)
                self.cur.y -= 3
                self.after_para = False
            elif t == "quote":
                self.ensure()
                self.cur.y -= 3
                self.paragraph(b["text"], indent=False, base="I", size=10.5, lead=13.6, left=22, right=22, after=6)
                self.after_para = False
            elif t == "list":
                self.ensure()
                self.cur.y -= 2
                for it in b["items"]:
                    words = to_words(runs(it))
                    lines = break_lines(words, TEXT_W - 22, TEXT_W - 22, SIZE, 22, 22, True)
                    self.put_lines(lines, LEAD)
                    for pg in reversed(self.pages[-2:]):                              # the bullet sits on the item's first line, wherever it landed
                        hit = next((op for op in pg.ops if op[0] == "line" and op[1] is lines[0]), None)
                        if hit:
                            pg.ops.append(("bullet", hit[2]))
                            break
                    self.cur.y -= 2
                self.cur.y -= 4
                self.after_para = False
            elif t == "hr":
                self.scene_break()
            i += 1


# ── turning pages into PDF ───────────────────────────────────────────────────────────────────────────────────

def _txt(x: float, y: float, style: str, size: float, text: str, tc: float = 0.0, gray: float = 0.0) -> str:
    return (f"BT {gray:g} g /F{FONT_NO[style]} {size:g} Tf " + f"{tc:g} Tc 0 Tw " + f"1 0 0 1 {x:.2f} {y:.2f} Tm ({esc(text)}) Tj ET")


def render_page(p: Page, recto: bool, book_title: str, show_number: bool) -> bytes:
    left = INNER if recto else OUTER
    ops: List[str] = []
    for op in p.ops:
        kind = op[0]
        if kind == "line":
            ln: Line = op[1]
            x = left + ln.x
            parts = [f"BT 0 g 1 0 0 1 {x:.2f} {op[2]:.2f} Tm 0 Tc {ln.tw:.3f} Tw "]
            for style, text in ln.pieces:
                parts.append(f"/F{FONT_NO[style]} {ln.size:g} Tf ({esc(text)}) Tj ")
            parts.append("ET")
            ops.append("".join(parts))
        elif kind == "center":
            ln = op[1]
            tracking = op[3]
            w = ln.width + tracking * sum(len(t) for _s, t in ln.pieces)
            x = left + (TEXT_W - w) / 2
            parts = [f"BT 0 g 1 0 0 1 {x:.2f} {op[2]:.2f} Tm {tracking:g} Tc 0 Tw "]
            for style, text in ln.pieces:
                parts.append(f"/F{FONT_NO[style]} {ln.size:g} Tf ({esc(text)}) Tj ")
            parts.append("ET")
            ops.append("".join(parts))
        elif kind == "rule":
            x = left + (TEXT_W - op[2]) / 2
            ops.append(f"0.45 g {x:.2f} {op[1]:.2f} {op[2]:g} 0.8 re f")
        elif kind == "stars":
            w = tw("*   *   *", "R", SIZE)
            ops.append(_txt(left + (TEXT_W - w) / 2, op[1], "R", SIZE, "*   *   *"))
        elif kind == "bullet":
            ops.append(_txt(left + 8, op[1], "R", SIZE, "\u2022"))
        elif kind == "toc":
            _k, y, indent, style, text, num = op
            size = 10.5
            nw = tw(num, "R", size)
            avail = TEXT_W - indent - nw - 14
            shown = text
            while tw(shown, style, size) > avail and len(shown) > 8:
                shown = shown[:-2]
            if shown != text:
                shown = shown.rstrip() + "..."
            ops.append(_txt(left + indent, y, style, size, shown))
            ops.append(_txt(left + TEXT_W - nw, y, "R", size, num))
            dots_x = left + indent + tw(shown, style, size) + 5
            dot_w = tw(". ", "R", size)
            count = int((left + TEXT_W - nw - 5 - dots_x) / dot_w)
            if count > 2:
                ops.append(_txt(dots_x, y, "R", size, ". " * count, gray=0.45))
        elif kind == "ctext":
            _k, y, style, size, text, tracking, gray = op
            w = tw(text, style, size) + tracking * len(text)
            ops.append(_txt(left + (TEXT_W - w) / 2, y, style, size, text, tracking, gray))
        elif kind == "ltext":
            _k, y, style, size, text, gray = op
            ops.append(_txt(left, y, style, size, text, 0, gray))
    if p.kind == "body":
        head = book_title if not recto else p.head
        if p.number and head and p.number > 0:
            hw = tw(head, "I", 8.5)
            ops.append(_txt(left + (TEXT_W - hw) / 2, H_PT - 40, "I", 8.5, head, gray=0.35))
    if show_number and p.kind in ("body", "opener"):
        s = str(p.number)
        ops.append(_txt(left + (TEXT_W - tw(s, "R", 9.5)) / 2, 38, "R", 9.5, s))
    return "\n".join(ops).encode("cp1252", "replace")


def _flate(raw: bytes) -> bytes:
    return zlib.compress(raw, 6)


def make_book(title: str, author: str, markdown: str, cover: Optional[bytes] = None, back_cover: Optional[bytes] = None,
              back_text: Optional[dict] = None, subtitle: str = "", publisher: str = "", year: Optional[int] = None) -> Tuple[bytes, int]:
    """-> (pdf bytes, number of pages). cover and back_cover are image files of any type; back_text {tagline, blurb, author, barcode_space}."""
    info, body_md = split_front(clean_notes(markdown))
    main_title = title.strip()
    if info.get("title"):
        main_title = info["title"]
    subtitle = subtitle.strip() or info.get("subtitle", "")
    if not info.get("title") and " - " in main_title and not subtitle:
        main_title, subtitle = [x.strip() for x in main_title.split(" - ", 1)]
    author = author.strip() or info.get("author", "")
    publisher = publisher.strip() or info.get("publisher", "")
    year = year or dt.date.today().year

    book = Book(f"{main_title}")
    book.run(block_split(body_md))
    body_pages = book.pages
    # front matter: title page, copyright page, contents (their pages are not numbered)
    front: List[Page] = []
    tp = Page("title")
    tp.y = H_PT * 0.66
    shell = Book(main_title)
    shell.cur = tp
    shell.centered(main_title, 28, "B", gap=8, lead=34)
    if subtitle:
        shell.centered(subtitle, 14, "I", gap=6, lead=19)
    shell.ornament(14)
    tp.y = H_PT * 0.22
    if author:
        shell.centered(author, 15, "R", gap=4, tracking=0.6)
    if publisher:
        shell.centered(publisher, 10, "I", gap=0)
    front.append(tp)
    cpage = Page("copy")
    y = 130.0
    for text, style, size in ([(f"Copyright \xa9 {year} {author}".strip(), "R", 9.5)] if author else []) + [("All rights reserved.", "R", 9.5)] + \
            ([(f"Published by {publisher}", "R", 9.5)] if publisher else []):
        cpage.ops.append(("ltext", y, style, size, text, 0.0))
        y -= 14
    front.append(cpage)
    entries = book.toc
    per_page = int((H_PT - 66 - 90 - BOT_Y) // 19)
    chunks = [entries[i:i + per_page] for i in range(0, len(entries), per_page)] or [[]]
    for ci, chunk in enumerate(chunks):
        pg = Page("toc")
        yy = H_PT - 100
        if ci == 0:
            title_ln = "Contents"
            pg.ops.append(("ctext", yy, "B", 16, title_ln, 1.5, 0.0))
            yy -= 34
        for kind, text, num in chunk:
            style = "B" if kind == "part" else "R"
            indent = 0 if kind in ("part", "front") else 14
            if kind == "part":
                yy -= 5
            pg.ops.append(("toc", yy, indent, style, cp(text), str(num)))
            yy -= 19
        front.append(pg)

    interior = front + body_pages
    # ── the PDF itself
    objs: List[bytes] = []

    def add(b: "str | bytes") -> int:
        objs.append(b if isinstance(b, bytes) else b.encode("latin-1"))
        return len(objs)

    add("<< /Type /Catalog /Pages 2 0 R >>")
    add("")
    fonts = {k: add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{FONT_NAME[k]} /Encoding /WinAnsiEncoding >>") for k in ("R", "B", "I", "BI")}
    hf1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    hf2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    gs = add("<< /Type /ExtGState /ca 0.58 /CA 0.58 >>")
    info_obj = add(f"<< /Title ({esc(cp(main_title))}) /Author ({esc(cp(author))}) /Creator (Aibussol) /Producer (Aibussol book typesetter) >>")
    font_res = "/Font << " + " ".join(f"/F{FONT_NO[k]} {fonts[k]} 0 R" for k in fonts) + " >>"
    kids: List[int] = []

    def page_obj(stream: bytes, resources: str) -> int:
        body = _flate(stream)
        cid = add(b"<< /Length " + str(len(body)).encode() + b" /Filter /FlateDecode >>\nstream\n" + body + b"\nendstream")
        return add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W_PT:g} {H_PT:g}] /Resources << {resources} >> /Contents {cid} 0 R >>")

    def image_page(img: Optional[Tuple[bytes, int, int]], overlay: str = "") -> int:
        if img:
            data, wpx, hpx = img
            iid = add(f"<< /Type /XObject /Subtype /Image /Width {wpx} /Height {hpx} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode("latin-1") + data + b"\nendstream")
            base, xo = f"q {W_PT:g} 0 0 {H_PT:g} 0 0 cm /Im0 Do Q\n", f"/XObject << /Im0 {iid} 0 R >> "
        else:
            base, xo = f"0.02 0.04 0.09 rg 0 0 {W_PT:g} {H_PT:g} re f\n", ""
        return page_obj((base + overlay).encode("cp1252", "replace"), f"{xo}/Font << /F1 {hf1} 0 R /F2 {hf2} 0 R >> /ExtGState << /GS1 {gs} 0 R >>")

    if cover:
        kids.append(image_page(pdfbase.prepare_image(cover, W_PT, H_PT)))
    for idx, p in enumerate(interior):
        recto = idx % 2 == 0
        kids.append(page_obj(render_page(p, recto, main_title, True), font_res))
    if back_cover or back_text:
        bt = back_text or {}
        has = bool(str(bt.get("tagline") or "").strip() or str(bt.get("blurb") or "").strip() or str(bt.get("author") or "").strip() or bt.get("barcode_space"))
        overlay = pdfbase.back_cover_ops(W_PT, H_PT, 54.0, str(bt.get("tagline") or ""), str(bt.get("blurb") or ""), str(bt.get("author") or ""), bool(bt.get("barcode_space"))) if has else ""
        kids.append(image_page(pdfbase.prepare_image(back_cover, W_PT, H_PT) if back_cover else None, overlay))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    objs[0] = f"<< /Type /Catalog /Pages 2 0 R /PageLayout /TwoPageRight >>".encode()
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {info_obj} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out), len(kids)
