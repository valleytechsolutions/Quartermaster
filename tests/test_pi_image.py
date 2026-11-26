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


def load_image_module(name):
    spec = importlib.util.spec_from_file_location(name, IMAGE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boot_config = load_image_module("boot_config")
first_login = load_image_module("first_login")
SERVICE_USER = re.search(r"^service_user=(\S+)$", SCRIPT.read_text(), re.M).group(1)


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
    assert unit["Before"] == ["quartermaster@%s.service" % SERVICE_USER]
    assert unit["ConditionPathExists"] == [boot_config.BOOT_FILE]
    installed = unit["ExecStart"][0].split()[-1]
    assert unit["ExecStart"] == ["/usr/bin/python3 -I " + installed]
    assert '"$mnt%s"' % installed in script
    assert '"$mnt/etc/systemd/system/quartermaster-boot-config.service"' in script


def test_the_polkit_rule_only_lets_the_service_user_request_wifi_scans():
    rule = (IMAGE / "50-quartermaster-wifi-scan.rules").read_text()
    assert re.findall(r'action\.id == "([^"]+)"', rule) == ["org.freedesktop.NetworkManager.wifi.scan"]
    assert re.findall(r'subject\.user == "([^"]+)"', rule) == [SERVICE_USER]
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
                 '"quartermaster@$service_user.service"'):
        assert unit in re.search(r"^units=\(([^)]*)\)", script, re.M).group(1)
    groups = re.search(r"^groups_wanted=\(([^)]*)\)", script, re.M).group(1).split()
    postinst = (ROOT / "packaging" / "postinst").read_text()
    postinst_groups = re.search(r"for grp in ([^;]*); do", postinst).group(1).split()
    assert set(postinst_groups) <= set(groups) and "quartermaster-usbreset" not in groups
    for forbidden in ("chpasswd", "passwd -d", "boot/firmware/ssh", "PermitRootLogin", "sudoers", "ssh-keygen -A",
                      "cloud-init clean", "systemctl mask", "cloud-init.disabled"):
        assert forbidden not in script, forbidden
    assert not re.search(r"(>|sed -i[^\n]*)\s*\"?\$mnt/boot/firmware/(cmdline\.txt|user-data|network-config)", script)
    assert 'check "cmdline.txt and cloud-init seed" "$(seeds)" "$seed_before"' in script


def shell_list(script, name):
    return re.search(r"^%s=\(([^)]*)\)" % name, script, re.M).group(1).split()


def test_the_image_logs_in_as_pi_with_the_default_password_and_keeps_the_other_accounts_locked():
    script = SCRIPT.read_text()
    assert re.search(r"^login_user=pi login_password=pi login_id=1000 host_name=quartermaster$", script, re.M)
    groups = shell_list(script, "login_groups")
    assert {"sudo", "adm", "gpio", "spi", "netdev", '"$service_user"'} <= set(groups)
    assert first_login.DEFAULT_USER == "pi"
    assert 'login_hash=$(openssl passwd -6 "$login_password")' in script
    assert 'in_chroot usermod --password "$login_hash" "$login_user"' in script
    assert 'in_chroot usermod --shell /bin/bash "$login_user"' in script
    assert 'die "the first user of the base image is $first_user, not $login_user"' in script
    assert """awk -F: '$2 !~ /^[!*]/ {print $1}' "$mnt/etc/shadow" | xargs)" "$login_user\"""" in script
    assert 'openssl passwd -6 -salt "$salt" "$login_password")" = "$stored"' in script
    for label in ('"root password locked"', '"$service_user password locked"', '"$service_user shell"',
                  '"$login_user in $group"', '"$login_user uid and shell"', '"default login record mode"',
                  '"first start keeps the default login"'):
        assert "check " + label in script, label
    assert "print(first_login.plan(*first_login.gather()))')\" \"[]\"" in script
    assert "umask 077 && printf" in script and first_login.HASH_FILE in script.replace('$mnt', "")


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")
def test_the_password_check_accepts_the_right_password_only():
    stored = subprocess.run(["openssl", "passwd", "-6", "pi"], capture_output=True, text=True, check=True).stdout.strip()
    salt = subprocess.run(["cut", "-d$", "-f3"], input=stored, capture_output=True, text=True, check=True).stdout.strip()

    def again(word):
        return subprocess.run(["openssl", "passwd", "-6", "-salt", salt, word], capture_output=True, text=True,
                              check=True).stdout.strip()

    assert "salt=$(cut -d'$' -f3 <<< \"$stored\")" in SCRIPT.read_text()
    assert stored.startswith("$6$") and again("pi") == stored and again("raspberry") != stored


def test_the_image_has_ssh_on_and_never_ships_host_keys():
    script = SCRIPT.read_text()
    assert "in_chroot systemctl enable ssh.service avahi-daemon.service getty@tty1.service" in script
    assert 'rm -f "$mnt"/etc/ssh/ssh_host_*' in script
    clean, checks = script.index('rm -f "$mnt"/etc/ssh/ssh_host_*'), script.index('log "Checking the image"')
    assert script.index("full-upgrade") < clean < checks
    for label in ('"ssh.service" "$(enabled ssh.service)" enabled', '"ssh host keys in the image"',
                  '"host keys are made on first start" "$(enabled regenerate_ssh_host_keys.service)" enabled',
                  '"first start marker" "$(cat "$mnt/etc/machine-id")" uninitialized', '"ssh password login"',
                  '"machine-id as in the base image"', '"ssh.socket off"'):
        assert "check " + label in script, label
    assert 'rm -f "$mnt"/tmp/probe_key*' in script and "etc/machine-id" in script
    keys = settings((IMAGE / "quartermaster-ssh-keys.service").read_text())
    assert keys["ConditionPathExistsGlob"] == ["!/etc/ssh/ssh_host_*_key"] and keys["Before"] == ["ssh.service"]
    assert keys["ExecStart"] == ["/usr/bin/ssh-keygen -A"] and keys["After"] == ["regenerate_ssh_host_keys.service"]
    assert "quartermaster-ssh-keys.service" in shell_list(script, "units")


def test_the_first_start_never_waits_for_a_user_prompt_but_still_takes_userconf_txt():
    script = SCRIPT.read_text()
    condition = settings((IMAGE / "userconfig-needs-userconf.conf").read_text())
    assert condition == {"ConditionPathExists": ["|/boot/firmware/userconf.txt", "|/boot/firmware/userconf",
                                                 "|" + first_login.BANNER]}
    assert 'grep -c \'^ConditionPathExists=|/boot/firmware/userconf\' ' in script
    assert '"$mnt/etc/systemd/system/userconfig.service.d/quartermaster.conf"' in script
    assert 'rm -f "$mnt/etc/ssh/sshd_config.d/rename_user.conf"' in script
    assert 'check "getty@tty1.service" "$(enabled getty@tty1.service)" enabled' in script
    assert "systemctl disable userconfig" not in script and "cancel-rename" not in script
    assert '"$mnt/etc/cloud/cloud.cfg.d/99_zz_quartermaster.cfg"' in script
    assert 'check "our cloud-init settings are read last" "$last_cfg" 99_zz_quartermaster.cfg' in script
    assert sorted(["99_raspberry-pi.cfg", "99_zz_quartermaster.cfg", "05_logging.cfg"])[-1] == "99_zz_quartermaster.cfg"
    for label in ('"cloud-init keeps SSH password logins"', '"the cloud-init seed sets no hostname"',
                  '"start order was examined"', '"start order has no cycle"'):
        assert "check " + label in script, label
    assert "systemd-analyze verify --man=no cloud-init.target default.target" in script
    cloud = (IMAGE / "99_zz_quartermaster.cfg").read_text()
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(cloud) == {"users": []}


def test_the_first_login_unit_runs_once_after_cloud_init_made_its_users_and_before_ssh():
    unit = settings((IMAGE / "quartermaster-first-login.service").read_text())
    assert unit["ConditionPathExists"] == ["!" + first_login.STAMP]
    assert unit["After"] == ["cloud-init-network.service cloud-init.service"]
    assert unit["Before"] == ["ssh.service systemd-user-sessions.service userconfig.service"]
    assert unit["Type"] == ["oneshot"] and unit["WantedBy"] == ["multi-user.target"]
    installed = unit["ExecStart"][0].split()[-1]
    assert unit["ExecStart"] == ["/usr/bin/python3 -I " + installed]
    script = SCRIPT.read_text()
    assert '"$mnt%s"' % installed in script and "quartermaster-first-login.service" in shell_list(script, "units")
    service_id = int(re.search(r"^service_id=(\d+)$", script, re.M).group(1))
    assert (first_login.FIRST_UID, first_login.SERVICE_UID) == (1000, service_id)
    assert first_login.DATA == re.search(r"^data=(\S+)$", script, re.M).group(1)
    assert first_login.BANNER == "/etc/ssh/sshd_config.d/rename_user.conf"


@pytest.mark.parametrize("text, wanted", [
    ("", None),
    ("#cloud-config\n# only comments\n", None),
    ("hostname: \"qm\"\nmanage_etc_hosts: true\n", None),
    ("users:\n- default\n", None),
    ("user:\n  name: \"kal\"\n  shell: /bin/bash\n  lock_passwd: false\n  passwd: \"$6$abc$def\"\n", ("kal", "$6$abc$def")),
    ("user:\n  name: pi\n  lock_passwd: true\n  ssh_authorized_keys:\n    - \"ssh-ed25519 AAAA\"\n", ("pi", None)),
    ("user: kal\n", ("kal", None)),
    ("users:\n- default\n- name: robot\n  hashed_passwd: $6$r$s\n", ("robot", "$6$r$s")),
    ("users:\n- name: [1]\n", None),
    ("users:\n  kal:\n    lock_passwd: false\n    ssh_authorized_keys: [\"ssh-ed25519 AAAA\"]\n", ("kal", None)),
    ("users:\n  default: true\n  robot: {passwd: $6$r$s}\n", ("robot", "$6$r$s")),
    ("users:\n  off: false\n  bare: {}\n", ("bare", None)),
    ("users: pi\n", ("pi", None)),
    ("users: \"default, kal\"\n", ("kal", None)),
    ("users:\n- default,kal\n", ("kal", None)),
    ("users:\n- name: default\n  lock_passwd: false\n", None),
    ("users:\n  1: true\n", None),
    ("user: [unclosed\n", None),
    ("- just\n- a list\n", None),
])
def test_the_first_login_reads_the_user_imager_asked_for(text, wanted):
    pytest.importorskip("yaml")
    assert first_login.requested(text) == wanted


PI = {"pi": (1000, "/home/pi", "/bin/bash"), "root": (0, "/root", "/bin/bash"),
      SERVICE_USER: (59999, "/var/lib/quartermaster", "/usr/sbin/nologin"),
      "nobody": (65534, "/nonexistent", "/usr/sbin/nologin")}
DEFAULT, OWN = "$6$salt$default", "$6$other$own"
LOCKED = {"root": "*", SERVICE_USER: "!"}


def login_plan(users, hashes, wanted, members=("pi",), links=("/home/pi/quartermaster",), banner=False):
    return first_login.plan(users, dict(LOCKED, **hashes), set(members), DEFAULT, wanted, set(links), banner)


def test_the_default_login_stays_when_imager_set_no_user():
    assert login_plan(PI, {"pi": DEFAULT}, None) == []
    assert login_plan(PI, {"pi": "!" + DEFAULT}, None) == [["usermod", "-U", "pi"]]
    assert login_plan(PI, {"pi": OWN}, None) == []
    assert login_plan(PI, {"pi": "!" + OWN}, None) == []


def test_a_user_from_imager_switches_the_default_login_off():
    users = dict(PI, kal=(1001, "/home/kal", "/bin/bash"))
    steps = login_plan(users, {"pi": DEFAULT, "kal": OWN}, ("kal", OWN))
    assert steps == [["usermod", "-L", "pi"], ["usermod", "-aG", SERVICE_USER, "kal"],
                     ["ln", "-s", first_login.DATA, "/home/kal/quartermaster"],
                     ["chown", "-h", "kal:", "/home/kal/quartermaster"]]
    assert login_plan(users, {"pi": DEFAULT, "kal": "!"}, ("kal", None))[0] == ["usermod", "-L", "pi"]
    assert login_plan(users, {"pi": "!" + DEFAULT, "kal": OWN}, ("kal", OWN), members=("pi", "kal"),
                      links=("/home/pi/quartermaster", "/home/kal/quartermaster")) == []
    assert ["usermod", "-L", "pi"] in login_plan(users, {"pi": DEFAULT, "kal": OWN}, None)
    assert ["usermod", "-L", "pi"] not in login_plan(users, {"pi": OWN, "kal": OWN}, ("kal", OWN))


def test_a_renamed_first_user_keeps_its_group_and_link_and_gets_the_chosen_password():
    users = {name: entry for name, entry in PI.items() if name != "pi"}
    users["kal"] = (1000, "/home/kal", "/bin/bash")
    assert login_plan(users, {"kal": OWN}, ("kal", OWN), members=("kal",), links=("/home/kal/quartermaster",)) == []
    assert login_plan(users, {"kal": DEFAULT}, ("kal", OWN), members=("kal",),
                      links=("/home/kal/quartermaster",)) == [["usermod", "-p", OWN, "kal"]]


def test_a_new_password_for_pi_from_imager_replaces_the_default():
    assert login_plan(PI, {"pi": DEFAULT}, ("pi", OWN)) == [["usermod", "-p", OWN, "pi"]]
    assert login_plan(PI, {"pi": "!" + DEFAULT}, ("pi", OWN)) == [["usermod", "-p", OWN, "pi"]]
    assert login_plan(PI, {"pi": OWN}, ("pi", OWN)) == []
    assert login_plan(PI, {"pi": "!" + DEFAULT}, ("pi", None)) == []
    assert login_plan(PI, {"pi": DEFAULT}, ("kal", OWN)) == []


def test_a_login_asked_for_without_a_password_never_keeps_the_default_one():
    assert login_plan(PI, {"pi": DEFAULT}, ("pi", None)) == [["usermod", "-L", "pi"]]
    users = {name: entry for name, entry in PI.items() if name != "pi"}
    users["kal"] = (1000, "/home/kal", "/bin/bash")
    renamed = dict(members=("kal",), links=("/home/kal/quartermaster",))
    assert login_plan(users, {"kal": DEFAULT}, ("kal", None), **renamed) == [["usermod", "-L", "kal"]]
    assert login_plan(users, {"kal": "!" + DEFAULT}, ("kal", None), **renamed) == []
    assert login_plan(users, {"kal": "!" + DEFAULT}, None, **renamed) == []
    assert login_plan(users, {"kal": DEFAULT}, ("kal", DEFAULT), **renamed) == [["usermod", "-L", "kal"]]
    assert login_plan(users, {"kal": DEFAULT}, None, **renamed) == [["usermod", "-L", "kal"]]
    assert login_plan(users, {"kal": DEFAULT}, ("pi", OWN), **renamed) == [["usermod", "-L", "kal"]]
    assert login_plan(PI, {"pi": "!" + DEFAULT}, first_login.requested("users:\n  pi:\n    shell: /bin/bash\n")) == []
    assert login_plan(PI, {"pi": "!" + DEFAULT}, first_login.requested("users: pi\n")) == []


def test_pi_set_up_in_imager_gets_its_group_and_link_back():
    assert login_plan(PI, {"pi": OWN}, ("pi", OWN), members=()) == [["usermod", "-aG", SERVICE_USER, "pi"]]
    assert login_plan(PI, {"pi": DEFAULT}, ("pi", OWN), members=(), links=()) == [
        ["usermod", "-p", OWN, "pi"], ["usermod", "-aG", SERVICE_USER, "pi"],
        ["ln", "-s", first_login.DATA, "/home/pi/quartermaster"], ["chown", "-h", "pi:", "/home/pi/quartermaster"]]


def test_a_login_without_a_home_folder_gets_no_link(tmp_path, monkeypatch):
    home = tmp_path / "home" / "pi"
    home.mkdir(parents=True)
    files = {"/etc/passwd": "pi:x:1000:1000::%s:/bin/bash\nkal:x:1001:1001::%s:/bin/bash\n" % (home, tmp_path / "gone"),
             "/etc/shadow": "pi:%s:1::::::\nkal:!:1::::::\n" % OWN,
             "/etc/group": "%s:x:59999:pi,kal\n" % SERVICE_USER}
    monkeypatch.setattr(first_login, "read", lambda path: files.get(path, ""))
    monkeypatch.setattr(first_login, "BANNER", str(tmp_path / "banner"))
    assert first_login.plan(*first_login.gather()) == [
        ["ln", "-s", first_login.DATA, "%s/quartermaster" % home], ["chown", "-h", "pi:", "%s/quartermaster" % home]]


def test_the_first_login_ignores_system_accounts_and_tidies_the_rename_banner():
    users = dict(PI, wizard=(999, "/home/w", "/bin/bash"), ghost=(1001, "/home/ghost", "/usr/sbin/nologin"))
    assert login_plan(users, {"pi": DEFAULT}, None) == []
    assert login_plan(PI, {"pi": DEFAULT}, None, banner=True) == [
        ["rm", "-f", first_login.BANNER], ["systemctl", "enable", "getty@tty1.service"],
        ["systemctl", "--no-block", "start", "getty@tty1.service"]]
    assert first_login.plan(PI, dict(LOCKED, pi=""), {"pi"}, "", None, {"/home/pi/quartermaster"}) == []


def test_the_first_login_marks_itself_done_only_when_every_step_worked(tmp_path, monkeypatch):
    stamp = tmp_path / "state" / "done"
    monkeypatch.setattr(first_login, "STAMP", str(stamp))
    monkeypatch.setattr(first_login, "gather", lambda: (PI, dict(LOCKED, pi="!" + DEFAULT), {"pi"}, DEFAULT, None,
                                                         {"/home/pi/quartermaster"}, False))
    ran = []
    monkeypatch.setattr(first_login.subprocess, "run",
                        lambda step, check: ran.append(step) or type("r", (), {"returncode": 1})())
    assert first_login.main() == 1 and ran == [["usermod", "-U", "pi"]] and not stamp.exists()
    monkeypatch.setattr(first_login.subprocess, "run", lambda step, check: type("r", (), {"returncode": 0})())
    assert first_login.main() == 0 and stamp.read_text() == "done\n"


def test_a_password_hash_is_not_printed(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(first_login, "STAMP", str(tmp_path / "done"))
    monkeypatch.setattr(first_login, "gather", lambda: (PI, dict(LOCKED, pi=DEFAULT), {"pi"}, DEFAULT, ("pi", OWN),
                                                         {"/home/pi/quartermaster"}, False))
    monkeypatch.setattr(first_login.subprocess, "run", lambda step, check: type("r", (), {"returncode": 0})())
    assert first_login.main() == 0
    assert capsys.readouterr().out == "usermod -p <hash> pi\n"


NOTICE = IMAGE / "default_password_notice.sh"


def notice(tmp_path, shadow, record=DEFAULT + "\n"):
    (tmp_path / "shadow").write_text(shadow)
    paths = [str(tmp_path / "shadow"), str(tmp_path / "record")]
    if record is not None:
        (tmp_path / "record").write_text(record)
    result = subprocess.run(["sh", str(NOTICE), *paths], capture_output=True, text=True)
    assert result.returncode == 0 and result.stderr == ""
    return result.stdout


@pytest.mark.skipif(not shutil.which("awk"), reason="needs awk")
def test_the_login_notice_names_only_logins_that_still_have_the_default_password(tmp_path):
    rest = ":20000:0:99999:7:::\n"
    system = "root:*" + rest + SERVICE_USER + ":!" + rest
    assert notice(tmp_path, system + "pi:" + DEFAULT + rest) == \
        "\nThe login pi still has the default password. Type passwd to set your own.\n\n"
    assert notice(tmp_path, system + "pi:!" + DEFAULT + rest) == ""
    assert notice(tmp_path, system + "pi:" + OWN + rest) == ""
    assert "The login pi kal still has" in notice(tmp_path, "pi:" + DEFAULT + rest + "kal:" + DEFAULT + rest)
    assert notice(tmp_path, "pi:" + DEFAULT + rest, record="") == ""
    assert notice(tmp_path, "pi:" + rest, record="\n") == ""
    (tmp_path / "record").unlink()
    assert notice(tmp_path, "pi:" + DEFAULT + rest, record=None) == ""


def test_the_login_notice_is_installed_where_every_login_shows_it():
    script = SCRIPT.read_text()
    assert NOTICE.read_text().startswith("#!/bin/sh\n") and first_login.HASH_FILE in NOTICE.read_text()
    assert ('install -D -m 755 "$here/packaging/image/default_password_notice.sh" '
            '"$mnt/etc/update-motd.d/20-quartermaster-password"') in script
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", "20-quartermaster-password")
    assert 'check "default password notice" "$(in_chroot /etc/update-motd.d/20-quartermaster-password | xargs)"' in script
    assert '"The login $login_user still has the default password. Type passwd to set your own."' in script
    assert 'check "the notice is shown at login"' in script and "pam_motd" in script


def test_pi_reaches_the_logs_through_the_group_and_a_link_in_its_home():
    script = SCRIPT.read_text()
    assert 'install -d -m 750 -o "$service_id" -g "$service_id" "$mnt$data" "$mnt$data/logs"' in script
    assert 'ln -sfn "$data" "$mnt$login_home/quartermaster"' in script
    assert 'check "logs link"' in script and 'check "$login_user reads the logs"' in script
    path = settings((IMAGE / "quartermaster-logs.path").read_text())
    assert path["PathChanged"] == [first_login.DATA + "/logs"] and path["WantedBy"] == ["multi-user.target"]
    service = settings((IMAGE / "quartermaster-logs.service").read_text())
    assert service["User"] == [SERVICE_USER] and service["Type"] == ["oneshot"]
    command = service["ExecStart"][0].split()
    assert command[:2] == ["-/usr/bin/find", first_login.DATA + "/logs"]
    assert command[-6:] == "-exec chmod -f g+rX {} +".split()
    assert 'check "$login_user reads a newly saved log"' in script


def test_a_log_saved_while_nothing_watched_or_in_a_burst_still_opens_to_the_group():
    service = settings((IMAGE / "quartermaster-logs.service").read_text())
    first, pause, second = service["ExecStart"]
    assert first == second and first.startswith("-/usr/bin/find ") and "-ignore_readdir_race" in first.split()
    assert pause == "/usr/bin/sleep 2"
    assert service["StartLimitIntervalSec"] == ["0"] and "WantedBy" not in service
    path = settings((IMAGE / "quartermaster-logs.path").read_text())
    assert path["Wants"] == ["quartermaster-logs.service"] and path["WantedBy"] == ["multi-user.target"]
    script = SCRIPT.read_text()
    assert "quartermaster-logs.path" in shell_list(script, "units") and "quartermaster-logs.service" in script


@pytest.mark.skipif(not shutil.which("find"), reason="needs find")
def test_the_logs_rule_opens_new_logs_to_the_group_and_then_changes_nothing(tmp_path):
    service = settings((IMAGE / "quartermaster-logs.service").read_text())
    logs = tmp_path / "logs"
    (logs / "sub").mkdir(parents=True)
    for name in ("a.wigle.csv", "sub/b.json"):
        (logs / name).write_text("x")
        (logs / name).chmod(0o600)
    (logs / "sub").chmod(0o700)
    logs.chmod(0o700)
    command = [str(logs) if part == first_login.DATA + "/logs" else part
               for part in service["ExecStart"][0].lstrip("-").split()]
    subprocess.run(command, check=True)
    assert [(logs / name).stat().st_mode & 0o777 for name in (".", "sub", "a.wigle.csv", "sub/b.json")] == \
        [0o750, 0o750, 0o640, 0o640]
    changed = subprocess.run(command[:-6] + ["-print"], capture_output=True, text=True, check=True).stdout
    assert changed == ""


def test_the_image_is_named_quartermaster_and_answers_to_quartermaster_local():
    script = SCRIPT.read_text()
    assert """printf '%s\\n' "$host_name" > "$mnt/etc/hostname\"""" in script
    assert "avahi-daemon" in script.split("--no-install-recommends install /tmp/quartermaster-pi.deb")[1].split("\n")[0]
    for label in ('"hostname" "$(cat "$mnt/etc/hostname")" "$host_name"', '"hostname in hosts"',
                  '"avahi-daemon.service" "$(enabled avahi-daemon.service)" enabled'):
        assert "check " + label in script, label


def test_the_image_only_turns_off_units_a_handheld_does_not_need():
    script = SCRIPT.read_text()
    unneeded, kept = shell_list(script, "unneeded"), shell_list(script, "kept")
    assert set(unneeded) == {"ModemManager.service", "triggerhappy.service", "triggerhappy.socket", "man-db.timer",
                             "apt-daily.timer", "apt-daily-upgrade.timer", "e2scrub_all.timer", "e2scrub_reap.service"}
    assert {"NetworkManager.service", "bluetooth.service", "systemd-timesyncd.service", "rpi-resize.service",
            "regenerate_ssh_host_keys.service", "cloud-init-local.service", "cloud-init-network.service",
            "cloud-config.service", "cloud-final.service"} <= set(kept)
    assert not set(unneeded) & set(kept)
    for word in ("NetworkManager", "bluetooth", "timesyncd", "cloud", "resize", "ssh", "avahi", "getty", "quartermaster"):
        assert not [unit for unit in unneeded if word in unit], word
    assert re.findall(r"systemctl disable (\S+)", script) == ['"$unit"']
    assert "printf 'disable %s\\n' \"${unneeded[@]}\" > \"$mnt/etc/systemd/system-preset/50-quartermaster.preset\"" in script
    assert 'check "$unit still on" "$(enabled "$unit")" enabled' in script
    assert 'check "root file system grows on first start" "$(enabled rpi-resize.service)" enabled' in script
    assert "overlay" not in script and "governor" not in script


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


def test_the_app_account_has_a_name_no_login_is_given_by_accident():
    script = SCRIPT.read_text()
    assert SERVICE_USER == first_login.GROUP == "_quartermaster"
    assert SERVICE_USER.startswith("_") and re.fullmatch(r"[a-z_][a-z0-9_]*", SERVICE_USER)
    assert not re.fullmatch(r"[a-z][a-z0-9-]*", SERVICE_USER)
    assert 'groupadd --gid "$service_id" "$service_user"' in script
    assert 'useradd --uid "$service_id" --gid "$service_user" --home-dir /var/lib/quartermaster' in script
    assert 'for taken in "$service_user" quartermaster; do' in script
    for label in ('"$service_user uid and group"', '"the name quartermaster is free for a login"',
                  '"the logs rule runs as $service_user"', '"the Wi-Fi scan rule is for $service_user"',
                  '"the boot file is read before the app starts"', '"radio helper accepts $service_user"',
                  '"systemd accepts the Quartermaster units"'):
        assert "check " + label in script, label
    assert not re.search(r"(useradd|groupadd|getent (passwd|group)|runuser -u|id -\w+|shadow_of) quartermaster\b",
                         script.replace('in "$service_user" quartermaster; do', "").replace(
                             "in_chroot getent passwd quartermaster; in_chroot getent group quartermaster", ""))
    unit = (ROOT / "packaging" / "quartermaster@.service").read_text()
    assert "User=%i\n" in unit and "%I" not in unit


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="needs systemd-analyze")
@pytest.mark.parametrize("user", ["_quartermaster", "pi", "kal-pi", "my_user"])
def test_systemd_takes_the_app_unit_for_any_user_name(tmp_path, user):
    units = tmp_path / "etc" / "systemd" / "system"
    units.mkdir(parents=True)
    shutil.copy(ROOT / "packaging" / "quartermaster@.service", units)
    result = subprocess.run(["systemd-analyze", "verify", "--man=no", "--root=%s" % tmp_path,
                             "quartermaster@%s.service" % user], capture_output=True, text=True)
    report = result.stdout + result.stderr
    if "nitialize manager" in report:
        pytest.skip("systemd-analyze cannot run here")
    assert "Invalid user" not in report and "fatal error" not in report, report


def test_the_image_unblocks_wifi_that_raspberry_pi_os_blocks_until_a_country_is_set():
    script = SCRIPT.read_text()
    assert '"$mnt"/var/lib/systemd/rfkill/*:wlan' in script and 'echo 0 > "$state"' in script
    assert "Wi-Fi rfkill saved state" in script


def test_the_radios_start_switched_on_and_the_image_sets_no_country():
    script = SCRIPT.read_text()
    rule = [line for line in (IMAGE / "radios-on.conf").read_text().splitlines() if not line.startswith("#")]
    assert rule == ["options rfkill default_state=1"]
    installed = re.search(r'radios-on\.conf" "\$mnt/etc/modprobe\.d/([^"]+)"', script).group(1)
    assert sorted(["rfkill_default.conf", installed])[-1] == installed and installed.endswith(".conf")
    assert "--show-depends rfkill" in script and "grep -o 'default_state=[0-9]*' | tail -n 1)\" default_state=1" in script
    assert "sed -i 's/^WirelessEnabled=.*/WirelessEnabled=true/'" in script
    for label in ('"radios start switched on with $kernel"', '"a kernel was checked for the radio switch"',
                  '"NetworkManager has Wi-Fi switched on"', '"the image sets no Wi-Fi country"'):
        assert "check " + label in script, label
    for forbidden in ("iw reg set", "do_wifi_country \"", "ieee80211_regdom=$", "REGDOMAIN="):
        assert forbidden not in script, forbidden
    assert "Wi-Fi and Bluetooth start switched on. No Wi-Fi country is set" in script


COUNTRY_NOTICE = IMAGE / "wifi_country_notice.sh"


def country_notice(*rules):
    result = subprocess.run(["sh", str(COUNTRY_NOTICE), *rules], capture_output=True, text=True)
    assert result.returncode == 0 and result.stderr == ""
    return result.stdout


def test_the_login_hint_for_the_wifi_country_shows_only_while_none_is_set():
    world = "global\ncountry 00: DFS-UNSET\n\t(2402 - 2472 @ 40), (N/A, 20), (N/A)\n\nphy#0\ncountry US: DFS-FCC\n"
    shown = country_notice(world)
    assert "No Wi-Fi country is set" in shown and shown.endswith("  sudo raspi-config nonint do_wifi_country US\n\n")
    assert shown.startswith("\n") and len(shown.splitlines()) == 6
    assert country_notice("global\ncountry US: DFS-FCC\n\nphy#0\ncountry 00: DFS-UNSET\n") == ""
    assert country_notice("country DE: DFS-ETSI\n") == ""
    assert country_notice("") == "" and country_notice("nl80211 not found.\n") == ""


def test_the_wifi_country_hint_is_installed_next_to_the_password_notice():
    script = SCRIPT.read_text()
    assert COUNTRY_NOTICE.stat().st_mode & 0o111 and COUNTRY_NOTICE.read_text().startswith("#!/bin/sh\n")
    assert ('install -D -m 755 "$here/packaging/image/wifi_country_notice.sh" '
            '"$mnt/etc/update-motd.d/21-quartermaster-wifi-country"') in script
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", "21-quartermaster-wifi-country")
    for label in ('"Wi-Fi country hint without a country"', '"Wi-Fi country hint with a country"',
                  '"raspi-config sets the Wi-Fi country"'):
        assert "check " + label in script, label
    assert "iw reg get" in COUNTRY_NOTICE.read_text() and "iw reg set" not in COUNTRY_NOTICE.read_text()


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
    with pytest.raises(ValueError, match="no image"):
        imager_os_list.os_list([], "o/r", "pi-v1.2.8")
    paths = []
    for hat in boot_config.PROFILES:
        paths.append(tmp_path / (hat + ".json"))
        paths[-1].write_text(json.dumps(meta(hat)))
    assert imager_os_list.main(["--repo", "o/r", "--tag", "pi-v1.2.8", *map(str, paths)]) == 0
    assert len(json.loads(capsys.readouterr().out)["os_list"]) == 2
    assert imager_os_list.main(["--repo", "o/r", "--tag", "pi-v1.2.8", "--manifest"]) == 0
    assert json.loads(capsys.readouterr().out) == imager_os_list.manifest("o/r", "pi-v1.2.8")
    for wrong in ([], ["--manifest", str(paths[0])]):
        with pytest.raises(SystemExit):
            imager_os_list.main(["--repo", "o/r", "--tag", "pi-v1.2.8", *wrong])
    capsys.readouterr()


def imager_shows(manifest, fetched, device):
    """What Raspberry Pi Imager lists for a chosen Pi model: it puts each fetched list in place of the entry that
    links to it, then keeps the images whose devices share a tag with the model and the folders that still hold one."""
    def merged(items):
        return [dict({k: v for k, v in item.items() if k != "subitems_url"},
                     subitems=fetched[item["subitems_url"]]["os_list"]) if item.get("subitems_url") in fetched
                else item for item in items]

    def kept(items):
        found = []
        for item in items:
            if "subitems" in item:
                inside = kept(item["subitems"])
                if inside:
                    found.append(dict(item, subitems=inside))
            elif "devices" in item:
                if set(item["devices"]) & set(device["tags"]):
                    found.append(item)
            elif device.get("matching_type") == "inclusive":
                found.append(item)
        return found
    return kept(merged(manifest["os_list"]))


def test_the_imager_manifest_names_the_pi_models_and_fetches_the_image_list():
    manifest = imager_os_list.manifest("owner/repo", "pi-v1.2.8")
    assert list(manifest) == ["imager", "os_list"] and list(manifest["imager"]) == ["devices"]
    devices = manifest["imager"]["devices"]
    names = [device["name"] for device in devices]
    assert names == ["Raspberry Pi Zero 2 W", "Raspberry Pi 3", "Raspberry Pi 4", "Raspberry Pi Zero W"]
    for device in devices:
        assert set(device) == {"name", "description", "tags"} and device["description"] and device["tags"]
    folder, = manifest["os_list"]
    assert folder["name"] == "Quartermaster 1.2.8" and folder["description"] and "subitems" not in folder
    assert folder["subitems_url"] == ("https://github.com/owner/repo/releases/download/pi-v1.2.8/"
                                     "quartermaster-imager.json")
    assert folder["icon"].endswith("/pi-v1.2.8/packaging/icon.png")
    assert "sha256" not in json.dumps(manifest) and "size" not in json.dumps(manifest)
    images = imager_os_list.os_list([meta("st7789-240"), meta("st7735-128")], "owner/repo", "pi-v1.2.8")
    fetched = {folder["subitems_url"]: images}
    for device in devices:
        shown, = imager_shows(manifest, fetched, device)
        assert [item["url"] for item in shown["subitems"]] == [item["url"] for item in images["os_list"]], device
    assert imager_shows(manifest, {}, devices[0]) == []
    assert imager_shows(manifest, fetched, {"tags": ["pi5-64bit"]}) == []
    for tag in ("v1.2.8", "pi-v1.2", "pi-v1.2.8-rc1"):
        with pytest.raises(ValueError, match="not a Pi release tag"):
            imager_os_list.manifest("owner/repo", tag)


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
    assert listing.count("tools/imager_os_list.py") == 2 and "--manifest" in listing
    assert '--manifest \\\n  > build/release/quartermaster.rpi-imager-manifest' in listing
    assert re.search(r"replace_release_assets\.sh \"\$TAG\" \*\.img\.xz \*\.img\.xz\.sha256 quartermaster-imager\.json"
                     r" \\\n\s+quartermaster\.rpi-imager-manifest$", listing, re.M)
    release = (WORKFLOWS / "release-pi.yml").read_text()
    assert 'index($package) != null and index("SHA256SUMS") != null' in release
    assert triggers["workflow_dispatch"]["inputs"]["tag"]["required"] is False


def test_a_practice_run_builds_the_images_from_the_branch_and_publishes_nothing():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((WORKFLOWS / "pi-images.yml").read_text())
    practice = workflow[True]["workflow_dispatch"]["inputs"]["practice"]
    assert practice["type"] == "boolean" and practice["default"] is False
    jobs = workflow["jobs"]
    assert jobs["publish"]["if"] == "${{ !inputs.practice }}" and "if" not in jobs["image"]
    pick = jobs["release"]["steps"][0]["run"]
    assert pick.index('[ "$PRACTICE" = true ]') < pick.index("exit 0") < pick.index('[ "$EVENT" = push ]')
    assert jobs["release"]["env"]["PRACTICE"] == "${{ inputs.practice }}"
    steps = {step.get("name"): step for step in jobs["image"]["steps"]}
    own = steps["Build the Pi package from this branch"]
    assert own["if"] == "inputs.practice" and "tools/build_deb.py --target pi --out build/pi" in own["run"]
    assert "gh " not in own["run"]
    assert steps["Wait for the Pi package"]["if"] == "${{ !inputs.practice }}"
    assert "gh release download" in steps["Wait for the Pi package"]["run"]
    named = steps["Name the package and the image"]
    assert "if" not in named and 'test -f "build/pi/quartermaster-pi_${VERSION}_all.deb"' in named["run"]
    order = [step.get("name") for step in jobs["image"]["steps"]]
    assert order.index("Wait for the Pi package") < order.index("Name the package and the image") < \
        order.index("Build the image")


def test_the_pi_release_can_be_run_by_hand_and_then_starts_the_image_build():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((WORKFLOWS / "release-pi.yml").read_text())
    triggers = workflow[True]
    assert triggers["push"] == {"tags": ["pi-v*"]}
    tag = triggers["workflow_dispatch"]["inputs"]["tag"]
    assert tag["required"] is True and "created from this branch if it does not exist" in tag["description"]
    assert workflow["permissions"] == {"contents": "write", "actions": "write"}
    assert workflow["concurrency"]["group"] == "release-${{ inputs.tag || github.ref_name }}"
    job = workflow["jobs"]["release"]
    assert job["env"]["TAG"] == "${{ inputs.tag || github.ref_name }}"
    steps = job["steps"]
    names = [step.get("name") for step in steps]
    assert names[-3:] == ["Test the app and build the Pi package", "Publish the Pi prerelease", "Start the image build"]
    check = steps[names.index("Check the Pi release tag")]["run"]
    assert 'test "$TAG" = "pi-v$version"' in check
    publish = steps[names.index("Publish the Pi prerelease")]["run"]
    by_hand, pushed = publish.index('[ "$GITHUB_EVENT_NAME" = workflow_dispatch ]'), publish.index("tag_option=(--verify-tag)")
    target = publish.index('tag_option=(--target "$(git rev-parse HEAD)")')
    assert by_hand < target < pushed < publish.index('gh release create "$TAG" "${tag_option[@]}" --prerelease --latest=false')
    assert publish.count("--verify-tag") == 1 and publish.count("gh release create") == 1
    assert 'git fetch --no-tags origin "refs/tags/$TAG"' in publish and "already exists on another commit" in publish
    start = steps[-1]
    assert start["if"] == "github.event_name == 'workflow_dispatch'"
    assert start["run"] == 'gh workflow run pi-images.yml --ref "$TAG" -f tag="$TAG"'
    assert start["env"] == {"GH_REPO": "${{ github.repository }}"}
    images = yaml.safe_load((WORKFLOWS / "pi-images.yml").read_text())
    assert "tag" in images[True]["workflow_dispatch"]["inputs"] and images[True]["push"] == {"tags": ["pi-v*"]}
    pick = images["jobs"]["release"]["steps"][0]["run"]
    assert 'elif [ -n "$REQUESTED" ]; then' in pick and images["jobs"]["release"]["env"]["REQUESTED"] == "${{ inputs.tag }}"


def test_the_diy_guide_offers_the_ready_image_before_the_manual_route():
    guide = (ROOT / "docs" / "diy.md").read_text()
    easy, manual = guide.index("## Easiest way: flash a ready image"), guide.index("## Assemble and install manually")
    assert easy < manual
    section = guide[easy:manual]
    version = json.loads((ROOT / "app-builder.json").read_text())["version"]
    for text in ("releases/download/pi-v%s/quartermaster.rpi-imager-manifest) and open it" % version,
                 "version 2 or newer", "Click **Quartermaster %s**" % version, "**Skip customisation**",
                 "**Localisation**: choose your capital city", "**Use custom file**", "**Apply & Restart**",
                 "shows no set-up pages for a file chosen this way", "`sudo nmtui`",
                 "quartermaster-pi-%s-st7735-128.img.xz" % version, "quartermaster-pi-%s-st7789-240.img.xz" % version,
                 "Use custom", "st7735-128.img.xz", "st7789-240.img.xz", "quartermaster.txt",
                 "quartermaster@%s.service" % SERVICE_USER, "/var/lib/quartermaster/quartermaster/logs", "Wi-Fi country",
                 "built-in `%s` account" % SERVICE_USER,
                 "8 GB or more", "switched on from the first start", "does not choose a country for you",
                 "leaves out 6 GHz", "`sudo raspi-config nonint do_wifi_country US`", "reminds you at each login"):
        assert text in section, text


def test_the_diy_guide_gives_the_default_login_and_says_to_change_the_password():
    guide = (ROOT / "docs" / "diy.md").read_text()
    section = guide[guide.index("## Easiest way: flash a ready image"):guide.index("## Assemble and install manually")]
    login = section[section.index("### Log in"):section.index("### Good to know")]
    for text in ("user `pi` with password `pi`", "SSH is on", "`ssh pi@quartermaster.local`", "`ssh pi@<address>`",
                 "Change the password right away", "`passwd`", "shared or public Wi-Fi",
                 "reminds you each time you log in",
                 "set in Raspberry Pi Imager replace the default", "`~/quartermaster/logs`", "host keys",
                 "`sudo usermod -aG %s <user>`" % SERVICE_USER):
        assert text in login, text
    assert login.index("user `pi` with password `pi`") < login.index("Change the password right away")
    assert "no password and SSH is off" not in guide
    script = SCRIPT.read_text()
    assert "login_user=pi login_password=pi" in script and "host_name=quartermaster" in script
