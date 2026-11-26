#!/usr/bin/env python3
"""Write a Raspberry Pi Imager repository (os_list JSON) for the image metadata files build_image.sh writes.

    python3 tools/imager_os_list.py --repo owner/name --tag pi-v1.2.8 build/images/*.json > os_list.json

In Imager, App Options > Content Repository > Use custom URL takes the published file's address."""

import argparse
import json
from pathlib import Path
import re
import sys

HATS = {"st7735-128": "1.44-inch LCD HAT (128x128)", "st7789-240": "1.3-inch LCD HAT (240x240)"}
DEVICES = ["pi4-32bit", "pi3-32bit", "pi1-32bit"]
ICON = "https://raw.githubusercontent.com/{repo}/{tag}/packaging/icon.png"
URL = "https://github.com/{repo}/releases/download/{tag}/{file}"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
        "devices": DEVICES,
        "website": "https://github.com/%s/blob/%s/docs/diy.md" % (repo, tag),
    }


def os_list(metas, repo, tag):
    entries = sorted((entry(meta, repo, tag) for meta in metas), key=lambda item: item["url"])
    if len({item["url"] for item in entries}) != len(entries):
        raise ValueError("the same image is listed twice")
    return {"os_list": entries}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("metadata", nargs="+", type=Path)
    args = parser.parse_args(argv)
    metas = [json.loads(path.read_text()) for path in args.metadata]
    json.dump(os_list(metas, args.repo, args.tag), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
