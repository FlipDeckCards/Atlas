"""Text out of an attached document (Word or plain text) so Helix and the crew's agents can read it.

Word files are read with the standard library only (a .docx is a zip of XML), so nothing new is installed. Old binary .doc, PDF and
anything else is refused with a plain message instead of being guessed at.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
import zipfile
from typing import Tuple
from xml.etree import ElementTree

MAX_DOC_BYTES = 3 * 1024 * 1024                         # one attached document, decoded (3 MB)
MAX_DOC_B64_CHARS = (MAX_DOC_BYTES * 4) // 3 + 8
MAX_DOC_TEXT_CHARS = 100_000                            # what is kept of it
CHAT_DOC_CHARS = 24_000                                # how much of it goes into one chat model call
MAX_XML_BYTES = 20 * 1024 * 1024                        # the unzipped document.xml, so a zip bomb is refused

TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".rtf")
ACCEPT = ".docx," + ",".join(TEXT_EXTENSIONS)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        info = zf.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("That is not a readable Word (.docx) file.")
    if info.file_size > MAX_XML_BYTES:
        raise ValueError("That Word file is too large to read.")
    try:
        root = ElementTree.fromstring(zf.read(info))
    except ElementTree.ParseError:
        raise ValueError("That is not a readable Word (.docx) file.")
    paras = []
    for p in root.iter(_W + "p"):
        parts = []
        for el in p.iter():
            if el.tag == _W + "t":
                parts.append(el.text or "")
            elif el.tag == _W + "tab":
                parts.append("\t")
            elif el.tag in (_W + "br", _W + "cr"):
                parts.append("\n")
        paras.append("".join(parts))
    return "\n".join(paras)


def extract_text(name: str, data: bytes) -> str:
    """The document's text, whitespace tidied and cut to MAX_DOC_TEXT_CHARS. Raises ValueError with a message for the user."""
    low = (name or "").lower()
    if len(data) > MAX_DOC_BYTES:
        raise ValueError("That document is too large (limit %d MB)." % (MAX_DOC_BYTES // (1024 * 1024)))
    if low.endswith(".docx"):
        text = _docx_text(data)
    elif low.endswith(TEXT_EXTENSIONS):
        if b"\x00" in data[:4096]:
            raise ValueError("That file is not plain text.")
        text = data.decode("utf-8", errors="replace")
    elif low.endswith(".doc"):
        raise ValueError("Old .doc files cannot be read. Save it as .docx or .txt and attach that.")
    else:
        raise ValueError("Attach a Word (.docx) or text (.txt, .md, .csv, .json) document, or an image.")
    text = re.sub(r"[ \t]+\n", "\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ValueError("I could not find any text in that document.")
    return text[:MAX_DOC_TEXT_CHARS]


def from_b64(name: str, b64: str) -> str:
    try:
        data = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError):
        raise ValueError("The document upload was damaged. Try attaching it again.")
    return extract_text(name, data)


def clean_name(name: str) -> str:
    """A file name safe to show and to save in a mission's outbox."""
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", (name or "").replace("\\", "/").split("/")[-1]).strip(" .")[:80]
    return base or "document.txt"


def outbox_name(name: str) -> Tuple[str, str]:
    """(display name, outbox file name): the outbox holds text, so a Word file is saved as .txt, prefixed so the crew can tell it is Michael's."""
    shown = clean_name(name)
    stem = shown.rsplit(".", 1)[0] if shown.lower().endswith(".docx") else shown
    return shown, "attached-" + (stem if stem.lower().endswith(TEXT_EXTENSIONS) else stem + ".txt")
