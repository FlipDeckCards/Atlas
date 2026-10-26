"""Append recoloured robot rows to crew/assets/bots.png for new roles, so they look like the rest of the crew.

    python tools/art/add_role_rows.py [--base support] role=#rrggbb ...

Each new row is a copy of the base role's row with its body hue set to the role's colour (shading kept). Rows are appended in the order given,
so add the same roles, in the same order, to BOT_ROWS in station.py. Prints the new BOT_VER. Needs Pillow and numpy.
"""

import argparse
import colorsys
import hashlib
import os

import numpy as np
from PIL import Image

SHEET = os.path.join(os.path.dirname(__file__), "..", "..", "src", "openjarvis", "server", "crew", "assets", "bots.png")
CELL = 64
ROWS = ['lead', 'researcher', 'builder', 'writer', 'designer', 'operator', 'reviewer', 'briefer', 'workbench',
        'marketer', 'social', 'support', 'analyst', 'qa', 'finance', 'weather', 'travel', 'event']


def recolor(row, hexcolor):
    r, g, b = (int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5))
    th, ts, _ = colorsys.rgb_to_hsv(r, g, b)
    hsv = np.array(row.convert("RGB").convert("HSV"), dtype=np.float32)
    tinted = hsv[..., 1] > 45                                # white and grey parts (eyes, joints) keep their colour
    hsv[..., 0] = np.where(tinted, th * 255, hsv[..., 0])
    hsv[..., 1] = np.where(tinted, np.clip(hsv[..., 1] * (ts / 0.6), 0, 255), hsv[..., 1])
    out = Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB").convert("RGBA")
    out.putalpha(row.getchannel("A"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="support")
    ap.add_argument("roles", nargs="+", help="role=#rrggbb")
    a = ap.parse_args()
    sheet = Image.open(SHEET).convert("RGBA")
    i = ROWS.index(a.base)
    base = sheet.crop((0, i * CELL, sheet.width, (i + 1) * CELL))
    out = Image.new("RGBA", (sheet.width, sheet.height + CELL * len(a.roles)), (0, 0, 0, 0))
    out.paste(sheet, (0, 0))
    for n, spec in enumerate(a.roles):
        role, color = spec.split("=")
        out.paste(recolor(base, color), (0, sheet.height + n * CELL))
    out.save(SHEET, optimize=True)
    print("BOT_VER =", hashlib.sha1(open(SHEET, "rb").read()).hexdigest()[:8])


if __name__ == "__main__":
    main()
