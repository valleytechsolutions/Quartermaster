"""Apply the display named in quartermaster.txt on the boot partition to /etc/quartermaster/display."""

import os
import sys

PROFILES = ("st7735-128", "st7789-240")
ALIASES = {"1.44": "st7735-128", "1.44in": "st7735-128", "128": "st7735-128",
           "1.3": "st7789-240", "1.3in": "st7789-240", "240": "st7789-240"}
BOOT_FILE = "/boot/firmware/quartermaster.txt"
DISPLAY_FILE = "/etc/quartermaster/display"


def parse(text):
    """The profile from the last display= line, None when there is none; ValueError for an unknown one."""
    chosen = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        key, sep, value = line.partition("=")
        if not sep or key.strip().lower() != "display":
            continue
        value = value.strip().strip("\"'").strip().lower()
        value = ALIASES.get(value, value)
        if value not in PROFILES:
            raise ValueError("unknown display %r, expected one of %s" % (value, ", ".join(PROFILES)))
        chosen = value
    return chosen


def apply(boot_file=BOOT_FILE, display_file=DISPLAY_FILE):
    try:
        with open(boot_file, "rb") as handle:
            text = handle.read(65536).decode("utf-8-sig", "replace")
    except FileNotFoundError:
        return "no %s, keeping the current display" % boot_file
    try:
        profile = parse(text)
    except ValueError as exc:
        return "%s: %s, keeping the current display" % (boot_file, exc)
    if profile is None:
        return "%s names no display, keeping the current one" % boot_file
    try:
        with open(display_file) as handle:
            current = handle.readline().strip()
    except FileNotFoundError:
        current = None
    if current == profile:
        return "display is %s" % profile
    temporary = display_file + ".new"
    with open(temporary, "w") as handle:
        handle.write(profile + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, display_file)
    return "display changed from %s to %s" % (current or "none", profile)


def main():
    print(apply())
    return 0


if __name__ == "__main__":
    sys.exit(main())
