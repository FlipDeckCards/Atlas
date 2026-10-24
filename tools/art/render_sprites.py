"""Turn Michael's Meshy 3D models into the flat, transparent pictures the Aibussol Station draws.

Run it with Blender (free, blender.org), no window needed:

    blender -b -P render_sprites.py -- "<folder with the art>" "<folder for the pictures>"

The art folder holds one folder or .zip per model: "Asteroid 1", "Asteroid 2", "Asteroid 3", "Satellite", "Helix" (each with its .fbx and
texture .png files inside, in any sub-folder). For each model this writes transparent PNGs:

    asteroid1.png asteroid2.png asteroid3.png   one three-quarter view each (256 px)
    satellite.png                               one three-quarter view (384 px)
    helix_ny.png helix_py.png helix_px.png helix_nx.png
                                                Helix seen from the four sides (256 px): from -Y, +Y, +X and -X. Which one is her front depends on
                                                how the model was made; the Station is set up from the pictures afterwards.

Works with Blender 3.6 to 5.x. Nothing is changed in the art files.
"""

import math
import os
import re
import sys
import tempfile
import zipfile

import bpy
from mathutils import Vector

SIZES = {"asteroid": 256, "satellite": 384, "helix": 256}


def log(*a):
    print("[sprites]", *a, flush=True)


def clean_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_fbx(path):
    before = set(bpy.data.objects)
    try:
        bpy.ops.import_scene.fbx(filepath=path)
    except (AttributeError, RuntimeError):
        bpy.ops.wm.fbx_import(filepath=path)               # Blender 5 moved the importer
    return [o for o in bpy.data.objects if o not in before]


def meshes(objs):
    return [o for o in objs if o.type == "MESH"]


def fix_textures(objs, folder):
    """Meshy's models name the colour map <model>_texture.png next to the FBX. If a material lost its picture, give it that one."""
    pngs = [f for f in os.listdir(folder) if f.lower().endswith(".png")]
    base = next((f for f in pngs if re.search(r"_texture\.png$", f, re.I)), None)
    if not base:
        return
    img = bpy.data.images.load(os.path.join(folder, base), check_existing=True)
    for o in meshes(objs):
        for slot in o.material_slots:
            m = slot.material
            if not m or not m.use_nodes:
                continue
            tree = m.node_tree
            if any(n.type == "TEX_IMAGE" and n.image and n.image.size[0] > 0 for n in tree.nodes):
                continue
            tex = tree.nodes.new("ShaderNodeTexImage")
            tex.image = img
            bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf:
                tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])


def bounds(objs):
    pts = []
    for o in meshes(objs):
        pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def centre_and_fit(objs):
    """Put the model's middle at the origin and make its longest side 2 units, so one camera setup suits every model."""
    root = bpy.data.objects.new("root", None)
    bpy.context.scene.collection.objects.link(root)
    for o in objs:
        if o.parent is None:
            o.parent = root
    bpy.context.view_layer.update()
    lo, hi = bounds(objs)
    size = max(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z) or 1.0
    s = 2.0 / size
    root.scale = (s, s, s)
    bpy.context.view_layer.update()
    lo, hi = bounds(objs)
    root.location = -(lo + hi) / 2
    bpy.context.view_layer.update()


def set_up_render(px):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 48
    sc.cycles.use_denoising = False
    sc.render.resolution_x = sc.render.resolution_y = px
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "Standard"
    w = bpy.data.worlds.new("w")
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.55, 0.62, 0.8, 1)           # cool fill light, like starlight bouncing about
    bg.inputs["Strength"].default_value = 0.7
    sc.world = w


def add_lights():
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = 4.0
    sun.angle = math.radians(12)
    o = bpy.data.objects.new("sun", sun)
    o.rotation_euler = (math.radians(55), math.radians(15), math.radians(-35))        # from the upper left, as the Station's own light falls
    bpy.context.scene.collection.objects.link(o)
    rim = bpy.data.lights.new("rim", "SUN")
    rim.energy = 1.2
    ro = bpy.data.objects.new("rim", rim)
    ro.rotation_euler = (math.radians(120), 0, math.radians(150))
    bpy.context.scene.collection.objects.link(ro)


def add_camera():
    cam = bpy.data.cameras.new("cam")
    cam.type = "ORTHO"
    cam.ortho_scale = 2.35
    co = bpy.data.objects.new("cam", cam)
    bpy.context.scene.collection.objects.link(co)
    bpy.context.scene.camera = co
    return co


def look_from(cam, direction, dist=8.0):
    d = Vector(direction).normalized()
    cam.location = d * dist
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()


def render(cam, direction, out):
    look_from(cam, direction)
    bpy.context.scene.render.filepath = out
    bpy.ops.render.render(write_still=True)
    log("wrote", out)


def find_models(root, tmp):
    """{asset name: folder holding its .fbx}. Folders and .zip files both work; 'Asteroid 1' becomes asteroid1."""
    found = {}
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        key = re.sub(r"[^a-z0-9]", "", os.path.splitext(name)[0].lower())
        if name.lower().endswith(".zip"):
            dest = os.path.join(tmp, key)
            with zipfile.ZipFile(path) as z:
                z.extractall(dest)
            path = dest
        elif not os.path.isdir(path):
            continue
        for d, _dirs, files in os.walk(path):
            fbx = [f for f in files if f.lower().endswith(".fbx")]
            if fbx:
                found[key] = os.path.join(d, fbx[0])
                break
    return found


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) != 2:
        sys.exit('Usage: blender -b -P render_sprites.py -- "<art folder>" "<output folder>"')
    root, out = argv
    os.makedirs(out, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        models = find_models(root, tmp)
        if not models:
            sys.exit("No .fbx models found in " + root)
        for key, fbx in models.items():
            kind = "asteroid" if key.startswith("asteroid") else "satellite" if key.startswith("satellite") else "helix" if key.startswith("helix") else ""
            if not kind:
                log("skipping", key)
                continue
            log("rendering", key, "from", fbx)
            clean_scene()
            objs = import_fbx(fbx)
            if not meshes(objs):
                log("no mesh in", fbx)
                continue
            fix_textures(objs, os.path.dirname(fbx))
            centre_and_fit(objs)
            set_up_render(SIZES[kind])
            add_lights()
            cam = add_camera()
            if kind == "helix":
                for tag, d in (("ny", (0, -1, 0.12)), ("py", (0, 1, 0.12)), ("px", (1, 0, 0.12)), ("nx", (-1, 0, 0.12))):
                    render(cam, d, os.path.join(out, f"helix_{tag}.png"))
            else:
                render(cam, (1.0, -1.0, 0.75), os.path.join(out, f"{key}.png"))
    log("done:", out)


if __name__ == "__main__":
    main()
