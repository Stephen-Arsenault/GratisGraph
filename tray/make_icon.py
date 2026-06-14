#!/usr/bin/env python3
"""
make_icon.py — render the GratisGraph "G" (web/app/favicon.svg) into:
  - resources/menubar_G.png   black template silhouette for the menu bar
                              (macOS recolours template images for light/dark)
  - resources/GratisGraph.icns  the .app bundle icon

macOS only. Uses the system NSImage SVG rasteriser (PyObjC) — no extra deps.
Run once before building, or whenever the favicon changes:
    python3 make_icon.py
"""
import io
import os
import subprocess

from AppKit import (
    NSImage, NSBitmapImageRep, NSGraphicsContext, NSDeviceRGBColorSpace,
    NSBitmapImageFileTypePNG, NSCompositingOperationSourceOver,
)
from Foundation import NSMakeRect, NSZeroRect
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SVG = os.path.join(ROOT, 'web', 'app', 'favicon.svg')
RES = os.path.join(HERE, 'resources')
os.makedirs(RES, exist_ok=True)


def render_svg(path: str, size: int) -> Image.Image:
    """Rasterise an SVG to an RGBA PIL image of size×size via NSImage."""
    img = NSImage.alloc().initWithContentsOfFile_(path)
    if img is None:
        raise SystemExit(f'NSImage could not load {path}')
    rep = NSBitmapImageRep.alloc().\
        initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, size, size, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    img.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(0, 0, size, size), NSZeroRect,
        NSCompositingOperationSourceOver, 1.0)
    NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None)
    return Image.open(io.BytesIO(bytes(data))).convert('RGBA')


def trim_square(im: Image.Image, pad_frac: float = 0.08) -> Image.Image:
    """Crop transparent margins, then re-centre on a square canvas with padding
    so the glyph fills the icon."""
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    side = max(im.size)
    pad = int(side * pad_frac)
    canvas = Image.new('RGBA', (side + 2 * pad, side + 2 * pad), (0, 0, 0, 0))
    off = ((canvas.size[0] - im.size[0]) // 2, (canvas.size[1] - im.size[1]) // 2)
    canvas.paste(im, off)
    return canvas


# ── menu-bar template: black where the glyph is opaque, clear elsewhere ──
g = trim_square(render_svg(SVG, 256))
alpha = g.split()[3]
silhouette = Image.new('RGBA', g.size, (0, 0, 0, 0))
silhouette.paste(Image.new('RGBA', g.size, (0, 0, 0, 255)), (0, 0), alpha)
silhouette.save(os.path.join(RES, 'menubar_G.png'))
print('wrote resources/menubar_G.png', silhouette.size)

# ── .icns for the app bundle (colour G, centred, transparent background) ──
master = trim_square(render_svg(SVG, 1024), pad_frac=0.16)
iconset = os.path.join(RES, 'GratisGraph.iconset')
os.makedirs(iconset, exist_ok=True)
for px, label in [(16, '16x16'), (32, '16x16@2x'), (32, '32x32'), (64, '32x32@2x'),
                  (128, '128x128'), (256, '128x128@2x'), (256, '256x256'),
                  (512, '256x256@2x'), (512, '512x512'), (1024, '512x512@2x')]:
    master.resize((px, px), Image.LANCZOS).save(os.path.join(iconset, f'icon_{label}.png'))
subprocess.run(['iconutil', '-c', 'icns', iconset,
                '-o', os.path.join(RES, 'GratisGraph.icns')], check=True)
print('wrote resources/GratisGraph.icns')
