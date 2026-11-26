import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from tools import imager_os_list

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "packaging" / "image"
SCRIPT = ROOT / "tools" / "build_image.sh"
WORKFLOWS = ROOT / ".github" / "workflows"
SHA = "ab" * 32


def load_boot_config():
    spec = importlib.util.spec_from_file_location("boot_config", IMAGE / "boot_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boot_config = load_boot_config()


def settings(text):
    found = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            found.setdefault(key.strip(), []).append(value.strip())
    return found


def test_the_boot_file_knows_exactly_the_hat_profiles_the_app_accepts():
    source = (ROOT / "quartermaster" / "__main__.py").read_text()
    listed = re.search(r'"--display", choices=\(([^)]*)\)', source).group(1)
    profiles = tuple(name.strip().strip('"') for name in listed.split(","))
    assert ("cardputer",) + boot_config.PROFILES == profiles
    assert set(boot_config.ALIASES.values()) == set(boot_config.PROFILES)


@pytest.mark.parametrize("text, profile", [
    ("display=st7735-128\n", "st7735-128"),
    ("# display=st7735-128\ndisplay = ST7789-240  # the big one\n", "st7789-240"),
    ("display=st7735-128\r\ndisplay=\"st7789-240\"\r\n", "st7789-240"),
    ("Display=1.44\n", "st7735-128"),
    ("display='1.3in'\n", "st7789-240"),
    ("# nothing chosen\nbrightness=3\nst7789-240\n", None),
    ("", None),
])
def test_the_boot_file_parser_takes_the_last_display_line(text, profile):
    assert boot_config.parse(text) == profile


@pytest.mark.parametrize("text", ["display=st7735\n", "display=\n", "display=cardputer\n",
                                  "display=st7789-240\ndisplay=hdmi\n"])
def test_the_boot_file_parser_refuses_an_unknown_display(text):
    with pytest.raises(ValueError, match="unknown display"):
        boot_config.parse(text)


def test_applying_the_boot_file_changes_the_display_only_for_a_valid_choice(tmp_path):
    boot, display = tmp_path / "quartermaster.txt", tmp_path / "display"
    display.write_text("st7735-128\n")
    assert "no " in boot_config.apply(str(boot), str(display))
    boot.write_bytes(b"\xef\xbb\xbfdisplay=st7789-240\r\n")
    assert boot_config.apply(str(boot), str(display)) == "display changed from st7735-128 to st7789-240"
    assert display.read_text() == "st7789-240\n" and display.stat().st_mode & 0o777 == 0o644
    assert boot_config.apply(str(boot), str(display)) == "display is st7789-240"
    boot.write_text("display=st7735-129\n")
    assert "keeping the current display" in boot_config.apply(str(boot), str(display))
    boot.write_text("# edited by hand\n")
    assert "names no display" in boot_config.apply(str(boot), str(display))
    assert display.read_text() == "st7789-240\n"
    display.unlink()
    boot.write_text("display=1.44\n")
    assert boot_config.apply(str(boot), str(display)) == "display changed from none to st7735-128"
    assert not list(tmp_path.glob("*.new"))


@pytest.mark.parametrize("profile", boot_config.PROFILES)
def test_the_boot_partition_template_selects_its_hat(profile):
    template = (IMAGE / "quartermaster.txt").read_text()
    assert template.count("@HAT@") == 1
    text = template.replace("@HAT@", profile)
    assert boot_config.parse(text) == profile
    for other in boot_config.PROFILES:
        assert other in text


def test_the_boot_config_unit_runs_before_the_app_and_points_at_the_installed_script():
    unit = settings((IMAGE / "quartermaster-boot-config.service").read_text())
    script = SCRIPT.read_text()
    assert unit["Type"] == ["oneshot"] and unit["WantedBy"] == ["multi-user.target"]
    assert unit["Before"] == ["quartermaster@quartermaster.service"]
    assert unit["ConditionPathExists"] == [boot_config.BOOT_FILE]
    installed = unit["ExecStart"][0].split()[-1]
    assert unit["ExecStart"] == ["/usr/bin/python3 -I " + installed]
    assert '"$mnt%s"' % installed in script
    assert '"$mnt/etc/systemd/system/quartermaster-boot-config.service"' in script


def test_the_polkit_rule_only_lets_the_service_user_request_wifi_scans():
    rule = (IMAGE / "50-quartermaster-wifi-scan.rules").read_text()
    assert re.findall(r'action\.id == "([^"]+)"', rule) == ["org.freedesktop.NetworkManager.wifi.scan"]
    assert re.findall(r'subject\.user == "([^"]+)"', rule) == ["quartermaster"]
    assert rule.count("polkit.Result.YES") == 1 and "indexOf" not in rule and "isInGroup" not in rule


def test_the_image_script_is_strict_cleans_up_and_keeps_imager_customisation_working():
    script = SCRIPT.read_text()
    assert script.startswith("#!/usr/bin/env bash\n") and "\nset -euo pipefail\n" in script
    assert SCRIPT.stat().st_mode & 0o111
    assert "trap cleanup EXIT" in script and "losetup -d" in script and "umount" in script
    assert "sha256sum --check --strict" in script and "https://downloads.raspberrypi.com/" in script
    assert "raspios-trixie-armhf-lite" in script
    assert "dtparam=spi=on" in script and "--no-install-recommends install /tmp/quartermaster-pi.deb" in script
    assert "useradd --system" not in script and "--shell /usr/sbin/nologin" in script
    assert 'useradd --uid "$service_id"' in script and "radiod.peer_allowed" in script
    for unit in ("quartermaster-radio.socket", "quartermaster-boot-config.service",
                 "quartermaster@quartermaster.service"):
        assert unit in re.search(r"^units=\(([^)]*)\)", script, re.M).group(1)
    groups = re.search(r"^groups_wanted=\(([^)]*)\)", script, re.M).group(1).split()
    postinst = (ROOT / "packaging" / "postinst").read_text()
    postinst_groups = re.search(r"for grp in ([^;]*); do", postinst).group(1).split()
    assert set(postinst_groups) <= set(groups) and "quartermaster-usbreset" not in groups
    for forbidden in ("chpasswd", "passwd -d", "systemctl enable ssh", "boot/firmware/ssh", "PermitRootLogin",
                      "sudoers", "userconf", "cloud-init clean"):
        assert forbidden not in script, forbidden
    assert not re.search(r"(>|sed -i[^\n]*)\s*\"?\$mnt/boot/firmware/cmdline\.txt", script)
    assert "accounts with a password" in script and "ssh.service ssh.socket" in script


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_the_image_script_parses_and_refuses_bad_arguments():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    for args in ([], ["--hat", "hdmi", "--deb", "x.deb"], ["--wat"]):
        result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)
        assert result.returncode == 2 and "--hat st7735-128" in result.stderr
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", str(SCRIPT)], check=True)


def test_the_service_account_is_one_the_radio_helper_accepts_and_leaves_room_for_login_users():
    script = SCRIPT.read_text()
    service_id = int(re.search(r"^service_id=(\d+)$", script, re.M).group(1))
    spec = importlib.util.spec_from_file_location("radiod_for_image", ROOT / "packaging" / "radiod.py")
    radiod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(radiod)
    import grp
    try:
        target = grp.getgrnam(radiod.GROUP).gr_gid
    except KeyError:
        target = 4242
        radiod.grp = type("grp", (), {"getgrnam": staticmethod(lambda name: type("g", (), {"gr_gid": target}))})
    assert radiod.peer_allowed(service_id, service_id, members=[service_id, target])
    assert not radiod.peer_allowed(999, 999, members=[999, target])
    assert service_id >= 1000 and service_id + 1 >= 60000
    assert "[UG]ID_MAX" in script and "LAST_[UG]ID" in script and "$((service_id - 1))" in script


def test_the_image_unblocks_wifi_that_raspberry_pi_os_blocks_until_a_country_is_set():
    script = SCRIPT.read_text()
    assert '"$mnt"/var/lib/systemd/rfkill/*:wlan' in script and 'echo 0 > "$state"' in script
    assert "Wi-Fi rfkill saved state" in script


REPLACE = ROOT / "tools" / "replace_release_assets.sh"
FAKE_GH = r"""#!/usr/bin/env bash
set -euo pipefail
store=$FAKE_RELEASE
echo "$*" >> "$store/calls"
case "$1 $2" in
    "api repos/o/r/releases/tags/pi-v1.2.8") echo 7 ;;
    "release upload")
        [ -z "${FAIL_ON:-}" ] || [[ $4 != *"$FAIL_ON"* ]] || exit 1
        name=$(basename "$4"); id=$(($(cat "$store/next_id") + 1)); echo "$id" > "$store/next_id"
        cp -L "$4" "$store/assets/$id"; echo "$name" > "$store/assets/$id.name" ;;
    "api --paginate")
        python3 -I -c 'import json, pathlib, sys
d = pathlib.Path(sys.argv[1])
print(json.dumps([{"id": int(f.stem), "name": f.read_text().strip()} for f in sorted(d.glob("*.name"))]))' \
            "$store/assets" > "$store/listing"
        want=$(sed -n 's/.*select(.name == "\(.*\)").*/\1/p' <<< "$5")
        python3 -I -c 'import json, sys
print("\n".join(str(a["id"]) for a in json.load(open(sys.argv[1])) if a["name"] == sys.argv[2]))' "$store/listing" "$want" ;;
    "api -X")
        id=${4##*/}
        if [ "$3" = DELETE ]; then rm "$store/assets/$id" "$store/assets/$id.name"
        else echo "${6#name=}" > "$store/assets/$id.name"; fi ;;
    *) echo "unexpected gh $*" >&2; exit 3 ;;
esac
"""


def fake_release(tmp_path, files):
    store = tmp_path / "release"
    (store / "assets").mkdir(parents=True)
    (store / "next_id").write_text("0")
    for number, (name, body) in enumerate(files.items(), 1):
        (store / "assets" / str(number)).write_text(body)
        (store / "assets" / ("%d.name" % number)).write_text(name)
    (store / "next_id").write_text(str(len(files)))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    return store, bin_dir


def assets(store):
    return {f.read_text().strip(): (store / "assets" / f.stem).read_text() for f in (store / "assets").glob("*.name")}


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("fail_on", ["", "quartermaster-imager.json"])
def test_release_assets_are_only_swapped_after_every_upload_succeeded(tmp_path, fail_on):
    import os
    store, bin_dir = fake_release(tmp_path, {"a.img.xz": "old a", "quartermaster-imager.json": "old list",
                                             "SHA256SUMS": "keep"})
    new = tmp_path / "new"
    new.mkdir()
    (new / "a.img.xz").write_text("new a")
    (new / "quartermaster-imager.json").write_text("new list")
    env = dict(os.environ, PATH="%s:%s" % (bin_dir, os.environ["PATH"]), GH_REPO="o/r", FAKE_RELEASE=str(store),
               FAIL_ON=fail_on)
    result = subprocess.run(["bash", str(REPLACE), "pi-v1.2.8", str(new / "a.img.xz"),
                             str(new / "quartermaster-imager.json")], env=env, capture_output=True, text=True)
    found = assets(store)
    if fail_on:
        assert result.returncode != 0
        assert found["a.img.xz"] == "old a" and found["quartermaster-imager.json"] == "old list"
    else:
        assert result.returncode == 0, result.stderr
        assert found == {"a.img.xz": "new a", "quartermaster-imager.json": "new list", "SHA256SUMS": "keep"}
        calls = (store / "calls").read_text().splitlines()
        uploads = [i for i, call in enumerate(calls) if call.startswith("release upload")]
        deletes = [i for i, call in enumerate(calls) if call.startswith("api -X DELETE")]
        renames = [i for i, call in enumerate(calls) if call.startswith("api -X PATCH")]
        assert len(uploads) == 2 and len(deletes) == 2 and len(renames) == 2
        assert max(uploads) < min(deletes)
        assert calls[renames[-1]].endswith("name=quartermaster-imager.json --silent")


def meta(hat, version="1.2.8", **extra):
    name = "quartermaster-pi-%s-%s" % (version, hat)
    found = dict(name=name, file=name + ".img.xz", version=version, hat=hat, init_format="cloudinit-rpi",
                 extract_size=4 << 30, extract_sha256=SHA, image_download_size=700 << 20,
                 image_download_sha256="cd" * 32, release_date="2026-11-26", base_image="raspios.img.xz")
    found.update(extra)
    return found


def test_the_imager_list_points_at_the_release_assets():
    listing = imager_os_list.os_list([meta("st7789-240"), meta("st7735-128")], "owner/repo", "pi-v1.2.8")
    assert list(listing) == ["os_list"]
    first, second = listing["os_list"]
    assert first["url"] == ("https://github.com/owner/repo/releases/download/pi-v1.2.8/"
                            "quartermaster-pi-1.2.8-st7735-128.img.xz")
    assert "1.44-inch" in first["name"] and "1.3-inch" in second["name"]
    for item in (first, second):
        assert set(item) >= {"name", "description", "icon", "url", "extract_size", "extract_sha256",
                             "image_download_size", "image_download_sha256", "release_date", "init_format"}
        assert item["init_format"] == "cloudinit-rpi" and item["image_download_size"] < 2 << 30
        assert item["icon"].endswith("/pi-v1.2.8/packaging/icon.png")
        assert set(item["devices"]) == {"pi4-32bit", "pi3-32bit", "pi1-32bit"}
    assert (ROOT / "packaging" / "icon.png").is_file()


@pytest.mark.parametrize("bad, message", [
    (meta("st7735-128", version="1.2.7"), "not pi-v1.2.8"),
    (meta("hdmi"), "unknown HAT"),
    (meta("st7735-128", extract_sha256="xyz"), "extract_sha256"),
    (meta("st7735-128", image_download_size=0), "image_download_size"),
])
def test_the_imager_list_refuses_metadata_that_does_not_match(bad, message):
    with pytest.raises(ValueError, match=message):
        imager_os_list.os_list([bad], "owner/repo", "pi-v1.2.8")


def test_the_imager_list_refuses_duplicates_and_writes_json(tmp_path, capsys):
    with pytest.raises(ValueError, match="twice"):
        imager_os_list.os_list([meta("st7735-128"), meta("st7735-128")], "o/r", "pi-v1.2.8")
    paths = []
    for hat in boot_config.PROFILES:
        paths.append(tmp_path / (hat + ".json"))
        paths[-1].write_text(json.dumps(meta(hat)))
    assert imager_os_list.main(["--repo", "o/r", "--tag", "pi-v1.2.8", *map(str, paths)]) == 0
    assert len(json.loads(capsys.readouterr().out)["os_list"]) == 2


def test_every_workflow_action_is_pinned_by_commit():
    for workflow in WORKFLOWS.glob("*.yml"):
        for line in workflow.read_text().splitlines():
            if line.strip().startswith(("uses:", "- uses:")):
                assert re.search(r"uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d", line), (workflow.name, line)


def test_the_image_workflow_builds_both_hats_on_tags_monthly_and_on_request():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((WORKFLOWS / "pi-images.yml").read_text())
    triggers = workflow[True]
    assert triggers["push"] == {"tags": ["pi-v*"]} and "workflow_dispatch" in triggers
    assert re.fullmatch(r"\d+ \d+ \d+ \* \*", triggers["schedule"][0]["cron"])
    assert workflow["permissions"] == {"contents": "write", "actions": "read"}
    image = workflow["jobs"]["image"]
    assert image["needs"] == "release" and image["runs-on"] == "ubuntu-24.04"
    assert tuple(image["strategy"]["matrix"]["hat"]) == boot_config.PROFILES
    steps = "\n".join(step.get("run", "") for step in image["steps"])
    assert 'sudo tools/build_image.sh --hat "$HAT" --deb "$DEB"' in steps
    assert "2147483648" in steps and "qemu-user-static" in steps
    build = steps.index("sudo tools/build_image.sh")
    assert build < steps.index('sudo chown -R "$(id -u):$(id -g)" build/images') < steps.index('> "$IMAGE.sha256"')
    assert "gh release" not in steps.replace("gh release download", "")
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == ["release", "image"] and "imager-list" not in workflow["jobs"]
    listing = "\n".join(step.get("run", "") for step in publish["steps"])
    assert "tools/imager_os_list.py" in listing and "--clobber" not in listing
    assert listing.index("tools/imager_os_list.py") < listing.index("tools/replace_release_assets.sh")
    assert re.search(r"replace_release_assets\.sh \"\$TAG\" \*\.img\.xz \*\.img\.xz\.sha256 quartermaster-imager\.json$",
                     listing, re.M)
    release = (WORKFLOWS / "release-pi.yml").read_text()
    assert 'index($package) != null and index("SHA256SUMS") != null' in release


def test_the_diy_guide_offers_the_ready_image_before_the_manual_route():
    guide = (ROOT / "docs" / "diy.md").read_text()
    easy, manual = guide.index("## Easiest way: flash a ready image"), guide.index("## Assemble and install manually")
    assert easy < manual
    section = guide[easy:manual]
    for text in ("Use custom", "st7735-128.img.xz", "st7789-240.img.xz", "quartermaster.txt",
                 "quartermaster@quartermaster.service", "/var/lib/quartermaster/quartermaster/logs", "Wi-Fi country"):
        assert text in section, text
