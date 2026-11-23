#!/usr/bin/env python3
"""Build the CardputerZero package: python3 tools/build_deb.py [--out build]

The layout follows the CardputerZero AppBuilder convention, so the result installs with apt
and publishes to the store with: czdev publish --deb build/quartermaster_<version>_all.deb
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parents[1]
PREFIX = "usr/share/APPLaunch"
MAINTAINER = "Valleytech Solutions <178694271+valleytechsolutions@users.noreply.github.com>"
# The AppStore installs with plain dpkg when Depends are met, so anything left in Recommends would never arrive.
DEPENDS = ("python3 (>= 3.9), python3-pil, python3-numpy, python3-serial, python3-dbus, iw, iproute2, "
           "ca-certificates, network-manager, bluez, alsa-utils, rtl-sdr")


def build(out):
    manifest = json.loads((HERE / "app-builder.json").read_text())
    pkg, version = manifest["package_name"], manifest["version"]
    stage = Path(tempfile.mkdtemp(prefix="deb-"))
    stage.chmod(0o755)
    try:
        app_dir = stage / PREFIX / "apps" / pkg
        shutil.copytree(HERE / "quartermaster", app_dir / "quartermaster",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        launcher = stage / PREFIX / "bin" / manifest["bin_name"]
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\ncd /%s/apps/%s || exit 1\nexec python3 -m quartermaster \"$@\"\n"
                            % (PREFIX, pkg))
        launcher.chmod(0o755)
        images = stage / PREFIX / "share" / "images"
        images.mkdir(parents=True)
        shutil.copy2(HERE / "packaging" / "icon.png", images / (pkg + ".png"))
        applications = stage / PREFIX / "applications"
        applications.mkdir(parents=True)
        (applications / (pkg + ".desktop")).write_text(
            "[Desktop Entry]\nName=%s\nExec=/%s/bin/%s\nTerminal=false\nSysplause=true\n"
            "Icon=share/images/%s.png\nType=Application\n" % (manifest["app_name"], PREFIX, manifest["bin_name"], pkg))
        tmpfiles = stage / "usr/lib/tmpfiles.d" / (pkg + ".conf")
        tmpfiles.parent.mkdir(parents=True)
        shutil.copy2(HERE / "packaging" / "quartermaster.tmpfiles", tmpfiles)
        doc = stage / "usr/share/doc" / pkg
        doc.mkdir(parents=True)
        notices = HERE / "quartermaster" / "assets" / "node-firmware-notices.txt"
        (doc / "copyright").write_text((HERE / "LICENSE").read_text(encoding="utf-8") + "\n\n" +
                                       notices.read_text(encoding="utf-8"), encoding="utf-8")
        control = stage / "DEBIAN"
        control.mkdir()
        size = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file()) // 1024
        (control / "control").write_text(
            "Package: %s\nVersion: %s\nArchitecture: all\nMaintainer: %s\nInstalled-Size: %d\n"
            "Depends: %s\nSection: APPLaunch\nPriority: optional\n"
            "Homepage: https://github.com/valleytechsolutions/Quartermaster\n"
            "Description: %s\n Wi-Fi and Bluetooth wardriver for the M5Stack CardputerZero.\n"
            % (pkg, version, MAINTAINER, size, DEPENDS, manifest["description"]))
        shutil.copy2(HERE / "packaging" / "postinst", control / "postinst")
        (control / "postinst").chmod(0o755)
        for path in stage.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            elif path.parent.name != "DEBIAN" and not os.access(path, os.X_OK):
                path.chmod(0o644)
        out.mkdir(parents=True, exist_ok=True)
        deb = out / ("%s_%s_all.deb" % (pkg, version))
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "-b", str(stage), str(deb)], check=True,
                       stdout=subprocess.DEVNULL)
        return deb
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "build"))
    args = parser.parse_args()
    deb = build(Path(args.out))
    print(deb)


if __name__ == "__main__":
    main()
