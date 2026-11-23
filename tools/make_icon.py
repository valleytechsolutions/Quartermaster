import io
import re
import sys
from pathlib import Path

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "brand" / "quartermaster-logo.svg"
GOLD = "#F5B301"


def layered_svg(rim):
    src = LOGO.read_text()
    m = re.search(r"<g transform=\"([^\"]+)\">(.*)</g>", src, re.S)
    transform, body = m.group(1), m.group(2)

    def widen(match):
        return 'stroke-width="%g"' % (float(match.group(1)) + rim)

    under = re.sub(r'fill="#[0-9A-Fa-f]{6}"', 'fill="%s"' % GOLD, body)
    under = re.sub(r'stroke="#[0-9A-Fa-f]{6}"', 'stroke="%s"' % GOLD, under)
    under = re.sub(r'stroke-width="([0-9.]+)"', widen, under)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">'
        '<g transform="%s">%s</g><g transform="%s">%s</g></svg>'
        % (transform, under, transform, body)
    )


def render(size, rim, margin):
    big = size * 8
    png = cairosvg.svg2png(bytestring=layered_svg(rim).encode(), output_width=big, output_height=big)
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    box = im.getchannel("A").getbbox()
    crop = im.crop(box)
    inner = int(round(big * (1 - 2 * margin)))
    scale = inner / max(crop.size)
    crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    canvas.paste(crop, ((big - crop.width) // 2, (big - crop.height) // 2), crop)
    return canvas.resize((size, size), Image.LANCZOS)


def main():
    render(100, 22, 0.04).save(ROOT / "packaging" / "icon-launcher.png", optimize=True)
    store = render(256, 16, 0.08)
    bg = Image.new("RGBA", store.size, (0, 0, 0, 255))
    bg.alpha_composite(store)
    bg.convert("RGB").save(ROOT / "packaging" / "icon.png", optimize=True)


if __name__ == "__main__":
    sys.exit(main())
