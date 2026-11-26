#!/usr/bin/env python3
"""Write the notes of a Raspberry Pi release:

    python3 tools/pi_release_notes.py --repo owner/name --tag pi-v1.2.9 --ref <commit> [--assets names.txt]

--ref is the commit the pictures are shown from, and this checkout must have it. They are pictures of the DIY
screens on both HATs from docs/screens/diy when that commit has them, and docs/screens/waveshare-preview.png when it
does not. --assets lists the files the release holds, one per line: the notes then name the image files it really
has, and the release must have both images and the Imager files. Without it the notes name the files the image
build makes."""

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HATS = (
    ("st7735-128", "1.44inch LCD HAT", "128×128", "ST7735S"),
    ("st7789-240", "1.3inch LCD HAT", "240×240", "ST7789"),
)
PICTURE = "docs/screens/waveshare-preview.png"
SCREEN = "docs/screens/diy/%s/%s.png"
GALLERY = (("menu", "the menu"), ("wardrive", "a wardrive"), ("hardware", "the Hardware screen"),
           ("aircraft", "the aircraft viewer"))
IMAGER = ("quartermaster.rpi-imager-manifest", "quartermaster-imager.json")
TAG = re.compile(r"^pi-v(\d+\.\d+\.\d+)$")
REF = re.compile(r"^[0-9a-f]{40}$")
PHOTO = "https://www.waveshare.com/media/catalog/product/1/_/%s-1.jpg"
ALL_FOLDERS_FROM = (1, 2, 9)


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


def screen(hat, name):
    return SCREEN % (hat.rsplit("-", 1)[1], name)


def committed(ref):
    found = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "-z", "--name-only", ref + "^{commit}", "--",
                            "docs/screens"], capture_output=True)
    if found.returncode:
        raise ValueError("this checkout has no commit %s" % ref)
    return set(found.stdout.decode().split("\0"))


def pictures(repo, ref):
    raw = "https://raw.githubusercontent.com/%s/%s/" % (repo, ref)
    held = committed(ref)
    if all(screen(hat, name) in held for hat, *_ in HATS for name, _ in GALLERY):
        shown = "%s and %s" % (", ".join(what for _, what in GALLERY[:-1]), GALLERY[-1][1])
        lines = ["Here is how %s look on each HAT." % shown, ""]
        for hat, label, pixels, chip in HATS:
            lines += ["**Waveshare %s**, %s screen" % (label, pixels), "",
                      " ".join("<img src='%s%s' width='200' alt='Quartermaster on the Waveshare %s: %s'>"
                               % (raw, screen(hat, name), label, what) for name, what in GALLERY), ""]
        return lines
    if PICTURE not in held:
        raise ValueError("commit %s has neither the pictures in docs/screens/diy nor %s" % (ref, PICTURE))
    return ["<img src='%s%s' width='520' alt='Quartermaster on both HATs: the menu, the scanning settings, a wardrive,"
            " the stop question and the Hardware screen. The 1.44inch HAT is on top and the 1.3inch HAT below.'>"
            % (raw, PICTURE), ""]


def folders(version):
    if tuple(int(part) for part in version.split(".")) < ALL_FOLDERS_FROM:
        return "- Saved drives are in `~/quartermaster/logs`."
    return ("- Saved drives are in `~/quartermaster/logs`, Recon and Drive Alert logs in `~/quartermaster/recon` and"
            " aircraft captures in `~/quartermaster/aircraft`.")


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
    looks = pictures(repo, ref)
    guide = "https://github.com/%s/blob/%s/docs/diy.md" % (repo, tag)
    changes = "https://github.com/%s/blob/%s/CHANGELOG.md#%s" % (repo, tag, version.replace(".", ""))
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
        *looks,
        "### Flash a ready SD card",
        "",
        "1. Install the newest [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer.",
        "2. Download `quartermaster.rpi-imager-manifest` below and open it. Imager opens with the Quartermaster images"
        " in its list.",
        "3. Choose your Pi model, then **Quartermaster %s**, then the image for your HAT." % version,
        "4. Choose your microSD card, 8 GB or more, and go through Imager's set-up pages. Give it your Wi-Fi network:"
        " a Zero W, Zero 2 W or 3B can only join a 2.4 GHz one. On **Localisation**, choose your capital city, so"
        " Wi-Fi uses the channels of your country. Then write the card.",
        "5. With the Pi off, put the HAT on the 40-pin header and plug in the GPS. On a Zero, use the port marked"
        " **USB** for the GPS and **PWR IN** for power.",
        "6. Put the card in the Pi and switch it on. The first start takes a few minutes, and the Pi may restart once."
        " Then Quartermaster opens on the HAT by itself.",
        "",
        "To write an image file yourself, download the image for your HAT under **Assets** below. The table above gives"
        " its name. Write it with **Use custom** in Imager or with balenaEtcher. The [guide](%s) says what is"
        " different then." % guide,
        "",
        "### Log in and controls",
        "",
        "- The images log in as user `pi` with password `pi` and have SSH on: `ssh pi@quartermaster.local`. Change"
        " the password with `passwd` after your first login. A user set in Raspberry Pi Imager replaces this login,"
        " so then log in as that user. A hostname set there replaces `quartermaster`.",
        "- Move the joystick through the menu. Click it or press **K1** to select. **K2** goes back, and **K3** opens"
        " the actions of the screen.",
        folders(version),
        "",
        "### What is new",
        "",
        "The [changelog](%s) lists what is new in %s. To update a card you already use, see **Updating** under"
        " [Good to know](%s#good-to-know) in the guide." % (changes, version, guide),
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
    try:
        sys.stdout.write(notes(args.repo, args.tag, args.ref, assets))
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
