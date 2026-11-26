#!/usr/bin/env python3
"""Write the two Raspberry Pi Imager files of a Pi release.

    python3 tools/imager_os_list.py --repo owner/name --tag pi-v1.2.8 build/images/*.json > quartermaster-imager.json
    python3 tools/imager_os_list.py --repo owner/name --tag pi-v1.2.8 --manifest > quartermaster.rpi-imager-manifest

The first is the list of the images, from the metadata files build_image.sh writes. The second is what a user opens:
Raspberry Pi Imager 2 starts with it on a double-click, takes its Pi models from it and fetches the list of images
through it, so a manifest saved earlier still matches images that were rebuilt later. Imager only offers its Wi-Fi,
country and user set-up for an image picked from a list."""

import argparse
import json
from pathlib import Path
import re
import sys

HATS = {"st7735-128": "1.44-inch LCD HAT (128x128)", "st7789-240": "1.3-inch LCD HAT (240x240)"}
BOARDS = (
    ("Raspberry Pi Zero 2 W", "Zero 2 W or Zero 2 WH", "pi3-32bit"),
    ("Raspberry Pi 3", "Model 3B or 3B+", "pi3-32bit"),
    ("Raspberry Pi 4", "Model 4B", "pi4-32bit"),
    ("Raspberry Pi Zero W", "The original Zero W or Zero WH", "pi1-32bit"),
)
LIST = "quartermaster-imager.json"
ICON = "https://raw.githubusercontent.com/{repo}/{tag}/packaging/icon.png"
URL = "https://github.com/{repo}/releases/download/{tag}/{file}"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TAG = re.compile(r"^pi-v(\d+\.\d+\.\d+)$")


def entry(meta, repo, tag):
    hat = meta["hat"]
    if hat not in HATS:
        raise ValueError("unknown HAT %r" % hat)
    if tag != "pi-v" + meta["version"]:
        raise ValueError("%s holds Quartermaster %s, not %s" % (meta["file"], meta["version"], tag))
    for key in ("extract_sha256", "image_download_sha256"):
        if not SHA256.match(meta[key]):
            raise ValueError("%s has no valid %s" % (meta["file"], key))
    for key in ("extract_size", "image_download_size"):
        if not isinstance(meta[key], int) or meta[key] <= 0:
            raise ValueError("%s has no valid %s" % (meta["file"], key))
    return {
        "name": "Quartermaster %s for the Waveshare %s" % (meta["version"], HATS[hat]),
        "description": "Raspberry Pi OS Lite (32-bit) that starts Quartermaster on the %s." % HATS[hat],
        "icon": ICON.format(repo=repo, tag=tag),
        "url": URL.format(repo=repo, tag=tag, file=meta["file"]),
        "extract_size": meta["extract_size"],
        "extract_sha256": meta["extract_sha256"],
        "image_download_size": meta["image_download_size"],
        "image_download_sha256": meta["image_download_sha256"],
        "release_date": meta["release_date"],
        "init_format": meta["init_format"],
        "devices": sorted({board for _, _, board in BOARDS}),
        "website": "https://github.com/%s/blob/%s/docs/diy.md" % (repo, tag),
    }


def os_list(metas, repo, tag):
    entries = sorted((entry(meta, repo, tag) for meta in metas), key=lambda item: item["url"])
    if not entries:
        raise ValueError("no image to list")
    if len({item["url"] for item in entries}) != len(entries):
        raise ValueError("the same image is listed twice")
    return {"os_list": entries}


def manifest(repo, tag):
    version = TAG.match(tag)
    if not version:
        raise ValueError("%s is not a Pi release tag" % tag)
    return {
        "imager": {
            "devices": [{"name": name, "description": models, "tags": [board]} for name, models, board in BOARDS],
        },
        "os_list": [{
            "name": "Quartermaster %s" % version.group(1),
            "description": "Wardriving on a Raspberry Pi with a Waveshare LCD HAT. Open this and choose the image"
                           " for your HAT.",
            "icon": ICON.format(repo=repo, tag=tag),
            "subitems_url": URL.format(repo=repo, tag=tag, file=LIST),
        }],
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--manifest", action="store_true")
    parser.add_argument("metadata", nargs="*", type=Path)
    args = parser.parse_args(argv)
    if args.manifest == bool(args.metadata):
        parser.error("give the image metadata files, or --manifest alone")
    if args.manifest:
        found = manifest(args.repo, args.tag)
    else:
        found = os_list([json.loads(path.read_text()) for path in args.metadata], args.repo, args.tag)
    json.dump(found, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
