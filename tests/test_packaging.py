import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

import pytest

try:
    from tests.vendor import install_path_policy
except ImportError:
    install_path_policy = None

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
# The maintainer script check of the store's process-web-submission.yml and validate-pr.yml, as a Python pattern.
DANGEROUS = re.compile(r"rm\s+(-[a-zA-Z]+\s+)*(/(\s|$)|/(bin|etc|usr|var|home|boot|lib))|(curl|wget)[^|]*\|\s*(ba)?sh"
                       r"|mkfs|/etc/(passwd|shadow|sudoers)|dd\s[^|]*of=/dev/")


def runs_as_root(text):
    """The store's check for a bundled system service that would run as root."""
    in_service, user, dynamic = False, None, False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            in_service = line.lower() == "[service]"
            continue
        if not in_service or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().lower(), value.strip()
        if key == "user":
            user = value
        elif key == "dynamicuser":
            dynamic = value.lower() in ("1", "yes", "true", "on")
    if dynamic:
        return False
    return not user or user in ("root", "0")


def unit(name):
    return (PACKAGING / name).read_text()


def settings(text):
    found = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            found.setdefault(key.strip(), []).append(value.strip())
    return found


def test_the_helper_service_runs_unprivileged_with_only_the_network_rights_it_needs():
    service = settings(unit("quartermaster-radio.service"))
    assert not runs_as_root(unit("quartermaster-radio.service"))
    assert service["DynamicUser"] == ["yes"] and service["User"] not in (["root"], ["0"])
    assert service["AmbientCapabilities"] == ["CAP_NET_ADMIN CAP_NET_RAW"]
    assert service["CapabilityBoundingSet"] == ["CAP_NET_ADMIN CAP_NET_RAW"]
    for key in ("NoNewPrivileges", "ProtectHome", "PrivateDevices", "ProtectKernelTunables", "ProtectKernelModules",
                "RestrictNamespaces", "MemoryDenyWriteExecute", "LockPersonality", "RestrictSUIDSGID"):
        assert service[key] == ["yes"], key
    assert service["ProtectSystem"] == ["strict"] and service["IPAddressDeny"] == ["any"]
    assert service["RestrictAddressFamilies"] == ["AF_UNIX AF_NETLINK AF_PACKET"]
    assert service["ExecStart"] == ["/usr/bin/python3 -I /usr/lib/quartermaster/radiod.py"]
    socket = settings(unit("quartermaster-radio.socket"))
    assert socket["ListenSequentialPacket"] == ["/run/quartermaster/radio.sock"]
    assert socket["SocketGroup"] == ["quartermaster-radio"] and socket["SocketMode"] == ["0660"]


def test_the_install_scripts_make_the_group_and_nothing_the_store_forbids():
    postinst, prerm = (PACKAGING / "postinst").read_text(), (PACKAGING / "prerm").read_text()
    for script in (postinst, prerm):
        assert script.startswith("#!/bin/sh\n") and "set -e" in script
        assert not any(DANGEROUS.search(line) for line in script.splitlines())
        for banned in ("sudoers", "polkit", "setcap", "chmod", "u+s", "iw reg", "/etc/"):
            assert banned not in script, banned
    assert postinst.index("addgroup --system quartermaster-radio") < postinst.index("usermod")
    assert "audio quartermaster-radio; do" in postinst
    assert "systemctl enable --now quartermaster-radio.socket" in postinst
    assert "disable --now quartermaster-radio.socket" in prerm
    assert DANGEROUS.search("rm -rf /etc/foo") and DANGEROUS.search("curl x | sh")
    rules = (PACKAGING / "70-quartermaster.rules").read_text()
    if install_path_policy:
        assert not install_path_policy.udev_rule_runs_programs(rules)
    assert 'ATTR{idVendor}=="0e8d", ATTR{idProduct}=="7961"' in rules


def test_the_store_listing_declares_the_background_service():
    manifest = json.loads((ROOT / "app-builder.json").read_text())
    permissions = manifest["store"]["permissions"]
    assert sorted(permissions) == sorted(["camera", "microphone", "imu", "network", "additional_hardware",
                                          "background_service", "external_display"])
    assert all(isinstance(v, bool) for v in permissions.values())
    assert permissions["background_service"] is True
    assert manifest["version"] == "1.2.12"
    from quartermaster import __version__
    assert __version__ == manifest["version"]
    assert "## 1.2.12" in (ROOT / "CHANGELOG.md").read_text()
    assert "## Deep scan" in (ROOT / "README.md").read_text()


def build(out):
    spec = importlib.util.spec_from_file_location("build_deb", ROOT / "tools" / "build_deb.py")
    build_deb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_deb)
    return build_deb.build(out)


def test_the_package_passes_the_store_checks(tmp_path):
    if not shutil.which("dpkg-deb"):
        pytest.skip("dpkg-deb is needed to build the package")
    deb = os.environ.get("QUARTERMASTER_STORE_DEB") or build(tmp_path)
    if install_path_policy is not None:
        assert install_path_policy.rejected_paths(str(deb), "quartermaster") == []
    tar = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(deb)], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as members:
        files = {m.name.lstrip("./"): m for m in members.getmembers() if not m.isdir()}
        assert "usr/share/APPLaunch/bin/quartermaster" in files
        assert "usr/share/APPLaunch/applications/quartermaster.desktop" in files
        assert not any(name.endswith(("/hat.py", "/square.py", "/quartermaster@.service")) for name in files)
        assert "etc/quartermaster/display" not in files and "usr/bin/quartermaster" not in files
        for name, member in files.items():
            assert not member.mode & 0o6000, name
            assert not (member.ischr() or member.isblk()), name
        for name in ("usr/lib/quartermaster/radiod.py", "usr/lib/systemd/system/quartermaster-radio.service",
                     "usr/lib/systemd/system/quartermaster-radio.socket",
                     "usr/lib/udev/rules.d/70-quartermaster.rules"):
            assert name in files, name
        services = [n for n in files if n.endswith(".service")]
        assert services and not any(runs_as_root(members.extractfile(files[n]).read().decode()) for n in services)
        assert members.extractfile(files["usr/lib/quartermaster/radiod.py"]).read() == \
            (PACKAGING / "radiod.py").read_bytes()
        copyright = members.extractfile(files["usr/share/doc/quartermaster/copyright"]).read().decode()
        assert "SquachWatch-CYD (GPL-3.0)" in copyright and "flock-you by colonelpanichacks (MIT)" in copyright
    control = tmp_path / "control"
    subprocess.run(["dpkg-deb", "-e", str(deb), str(control)], check=True)
    for script in ("postinst", "prerm"):
        path = control / script
        assert path.stat().st_mode & 0o111, script
        assert not any(DANGEROUS.search(line) for line in path.read_text().splitlines())
    fields = subprocess.run(["dpkg-deb", "-f", str(deb), "Version", "Architecture"], capture_output=True,
                            text=True, check=True).stdout
    assert "Version: 1.2.12" in fields and "Architecture: all" in fields
    depends = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Depends"], text=True)
    for package in ("firmware-realtek", "firmware-mediatek", "firmware-atheros"):
        assert package + " | gh0st-wifi" in depends.strip().split(", ")
    assert not any(pkg in depends for pkg in ("luma.lcd", "gpiozero", "rpi-lgpio", "rpi.gpio"))
    unpacked = tmp_path / "unpacked"
    subprocess.run(["dpkg-deb", "-x", str(deb), str(unpacked)], check=True)
    app_dir = unpacked / "usr/share/APPLaunch/apps/quartermaster"
    subprocess.run([sys.executable, "-c", "from quartermaster.ui.app import App"], cwd=app_dir, check=True)
