#!/usr/bin/env python3
"""Write the notes of a Raspberry Pi release:

    python3 tools/pi_release_notes.py --repo owner/name --tag pi-v1.2.9 --ref <commit> [--assets names.txt]

--ref is the commit the pictures are shown from. --assets lists the files the release holds, one per line: the notes
then name the image files it really has, and the release must have both images and the Imager files. Without it the
notes name the files the image build makes."""

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
HATS = (
    ("st7735-128", "1.44inch LCD HAT", "128×128", "ST7735S"),
    ("st7789-240", "1.3inch LCD HAT", "240×240", "ST7789"),
)
PICTURE = "docs/screens/waveshare-preview.png"
IMAGER = ("quartermaster.rpi-imager-manifest", "quartermaster-imager.json")
TAG = re.compile(r"^pi-v(\d+\.\d+\.\d+)$")
REF = re.compile(r"^[0-9a-f]{40}$")
PHOTO = "https://www.waveshare.com/media/catalog/product/1/_/%s-1.jpg"


def page(label):
    return label.lower().replace(" ", "-")


def image_files(version, hat, label):
    return ("quartermaster-pi-%s-waveshare-%s.img.xz" % (version, page(label)),
            "quartermaster-pi-%s-%s.img.xz" % (version, hat))


def images(version, assets=None):
    found = []
    for hat, label, pixels, chip in HATS:
        names = image_files(version, hat, label)
        if assets is None:
            found.append((label, pixels, chip, names[0]))
            continue
        held = [name for name in names if name in assets and name + ".sha256" in assets]
        if not held:
            raise ValueError("the release has no image with its .sha256 for the Waveshare %s" % label)
        found.append((label, pixels, chip, held[0]))
    if assets is not None:
        for name in IMAGER:
            if name not in assets:
                raise ValueError("the release has no %s" % name)
    return found


def notes(repo, tag, ref, assets=None):
    version = TAG.match(tag)
    if not version:
        raise ValueError("%s is not a Pi release tag" % tag)
    if not REF.match(ref):
        raise ValueError("%s is not a commit" % ref)
    version = version.group(1)
    rows = ["| <img src='%s' width='160' alt='The Waveshare %s'><br>**Waveshare %s**<br><sub>%s screen, %s</sub> | `%s` |"
            % (PHOTO % page(label), label, label, pixels, chip, name)
            for label, pixels, chip, name in images(version, assets)]
    picture = "https://raw.githubusercontent.com/%s/%s/%s" % (repo, ref, PICTURE)
    guide = "https://github.com/%s/blob/%s/docs/diy.md" % (repo, tag)
    added = "" if assets is not None else "The images are added to this release shortly after it is published. "
    return "\n".join([
        "**This is the DIY version of Quartermaster**, for a Raspberry Pi with a Waveshare 1.44inch or 1.3inch LCD HAT."
        " Looking for the M5Stack CardputerZero version? Install it from the CardputerZero AppStore, or get it from"
        " the [latest release](https://github.com/%s/releases/latest)." % repo,
        "",
        "| Your HAT | Its image |",
        "| --- | --- |",
        *rows,
        "",
        "Photos by Waveshare.",
        "",
        "### What it looks like",
        "",
        "<img src='%s' width='520' alt='Quartermaster on both HATs: the menu, the scanning settings, a wardrive, the"
        " stop question and the Hardware screen. The 1.44inch HAT is on top and the 1.3inch HAT below.'>" % picture,
        "",
        "### Flash a ready SD card",
        "",
        "1. Install the newest [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer.",
        "2. Download `quartermaster.rpi-imager-manifest` below and open it. Imager opens with the Quartermaster images"
        " in its list.",
        "3. Choose your Pi model, then **Quartermaster %s**, then the image for your HAT." % version,
        "4. Choose your microSD card, 8 GB or more, and go through Imager's set-up pages. Give it your Wi-Fi network:"
        " a Zero W, Zero 2 W or 3B can only join a 2.4 GHz one. On **Localisation**, choose your capital city, so"
        " Wi-Fi uses the channels of your country.",
        "5. Put the card in the Pi with the HAT on and switch it on. The first start takes a few minutes. Then"
        " Quartermaster opens on the HAT by itself.",
        "",
        "To write an image file yourself, download the one for your HAT above and write it with **Use custom** in"
        " Imager or with balenaEtcher. The [guide](%s) says what is different then." % guide,
        "",
        "### Log in and controls",
        "",
        "- The images log in as user `pi` with password `pi` and have SSH on: `ssh pi@quartermaster.local`. Change"
        " the password with `passwd` after your first login.",
        "- Move the joystick through the menu. Click it or press **K1** to select. **K2** goes back, and **K3** opens"
        " the actions of the screen.",
        "- Saved drives are in `~/quartermaster/logs`.",
        "",
        "The [DIY build and installation guide](%s) has the parts list and every step. %sWhile this is the newest Pi"
        " release, its images are rebuilt every month with the latest Raspberry Pi OS updates." % (guide, added),
        "",
    ])


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--assets", type=Path)
    args = parser.parse_args(argv)
    assets = None
    if args.assets:
        assets = {line.strip() for line in args.assets.read_text().splitlines() if line.strip()}
    if not (ROOT / PICTURE).is_file():
        parser.error("%s is missing" % PICTURE)
    try:
        sys.stdout.write(notes(args.repo, args.tag, args.ref, assets))
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
