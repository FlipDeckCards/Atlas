"""Crop, shrink and arrange the renders from render_sprites.py into the files the Station loads (src/openjarvis/server/crew/assets/art/).

    python tools/art/make_station_art.py <folder with the renders> [--out <art folder>] [--right px|nx]

Needs Pillow. Writes asteroid1-3.png, satellite.png and helix-views.png (four square pictures in a row: front, back, facing right, facing
left). Helix's front is the render from -Y (helix_ny); change --front if the model was made facing the other way. --right says which side render
shows her facing the right of the screen. Each file is trimmed to its shape and kept small, so the Station stays quick.
"""

import argparse
import os

from PIL import Image

LIMITS = {"asteroid": 220, "satellite": 300}
CELL = 192


def trim(im, pad=4):
    im = im.convert("RGBA")
    box = im.getbbox()
    if not box:
        raise SystemExit("A picture is empty.")
    return im.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad)))


def fit(im, side):
    r = side / max(im.size)
    return im.resize((max(1, round(im.width * r)), max(1, round(im.height * r))), Image.LANCZOS) if r < 1 else im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("renders")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "openjarvis", "server", "crew", "assets", "art"))
    ap.add_argument("--front", default="ny", choices=["ny", "py", "px", "nx"])
    ap.add_argument("--right", default="px", choices=["px", "nx", "ny", "py"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    made = []
    for name, kind in [("asteroid1", "asteroid"), ("asteroid2", "asteroid"), ("asteroid3", "asteroid"), ("satellite", "satellite")]:
        src = os.path.join(a.renders, name + ".png")
        if os.path.isfile(src):
            fit(trim(Image.open(src)), LIMITS[kind]).save(os.path.join(a.out, name + ".png"), optimize=True)
            made.append(name + ".png")
    views = {t: os.path.join(a.renders, f"helix_{t}.png") for t in ("ny", "py", "px", "nx")}
    if all(os.path.isfile(v) for v in views.values()):
        opposite = {"ny": "py", "py": "ny", "px": "nx", "nx": "px"}
        front = a.front
        order = [front, opposite[front], a.right, opposite[a.right]]                # front, back, facing right, facing left
        ims = [trim(Image.open(views[t])) for t in order]
        top = max(i.height for i in ims)                                            # one scale for all four, feet on a common line
        scale = (CELL - 8) / top
        sheet = Image.new("RGBA", (CELL * 4, CELL), (0, 0, 0, 0))
        for k, im in enumerate(ims):
            im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
            sheet.alpha_composite(im, (k * CELL + (CELL - im.width) // 2, CELL - 4 - im.height))
        sheet.save(os.path.join(a.out, "helix-views.png"), optimize=True)
        made.append("helix-views.png")
    for f in made:
        print(f, os.path.getsize(os.path.join(a.out, f)) // 1024, "KB")


if __name__ == "__main__":
    main()
