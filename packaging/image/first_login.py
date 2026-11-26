"""First start of a ready image: make the login chosen in Raspberry Pi Imager the only one.

The image ships the login pi with a default password. When Imager set up a user, that default must not stay
usable. When Imager set up nothing, the default login must still work after Raspberry Pi OS's own first start."""

import os
import subprocess
import sys

DEFAULT_USER = "pi"
GROUP = "quartermaster"
DATA = "/var/lib/quartermaster/quartermaster"
HASH_FILE = "/usr/local/lib/quartermaster-image/default-login"
STAMP = "/var/lib/quartermaster-image/first-login.done"
USER_DATA = "/boot/firmware/user-data"
BANNER = "/etc/ssh/sshd_config.d/rename_user.conf"
USERCONF_FILES = ("/boot/firmware/userconf.txt", "/boot/firmware/userconf")
FIRST_UID, SERVICE_UID = 1000, 59999
NO_LOGIN = ("nologin", "false")


def requested(text):
    """The (name, password hash) cloud-init was asked to set up, or None."""
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    entries = [data.get("user")]
    if isinstance(data.get("users"), list):
        entries += data["users"]
    for entry in entries:
        if isinstance(entry, str) and entry != "default":
            return entry, None
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            found = entry.get("hashed_passwd") or entry.get("passwd")
            return entry["name"], found if isinstance(found, str) and found else None
    return None


def plan(users, hashes, members, default_hash, wanted, links=(), banner=False):
    """The commands that leave one working login. users maps a name to (uid, home, shell)."""
    name, new_hash = wanted or (None, None)
    steps = []
    logins = sorted(user for user, (uid, _, shell) in users.items()
                    if FIRST_UID <= uid < SERVICE_UID and not shell.endswith(NO_LOGIN))
    for user in logins:
        stored = hashes.get(user, "")
        if not default_hash or stored.lstrip("!") != default_hash:
            continue
        locked = stored.startswith("!")
        if user == name and new_hash and new_hash != default_hash:
            steps.append(["usermod", "-p", new_hash, user])
        elif user == name or len(logins) > 1:
            if not locked:
                steps.append(["usermod", "-L", user])
        elif name is None and locked and user == DEFAULT_USER:
            steps.append(["usermod", "-U", user])
    for user in logins:
        if user not in members:
            steps.append(["usermod", "-aG", GROUP, user])
        link = users[user][1].rstrip("/") + "/quartermaster"
        if link not in links:
            steps.append(["ln", "-s", DATA, link])
            steps.append(["chown", "-h", "%s:" % user, link])
    if banner:
        steps.append(["rm", "-f", BANNER])
        steps.append(["systemctl", "enable", "getty@tty1.service"])
        steps.append(["systemctl", "--no-block", "start", "getty@tty1.service"])
    return steps


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def gather():
    users, hashes, members = {}, {}, set()
    for line in read("/etc/passwd").splitlines():
        field = line.split(":")
        if len(field) >= 7 and field[2].isdigit():
            users[field[0]] = (int(field[2]), field[5], field[6])
    for line in read("/etc/shadow").splitlines():
        field = line.split(":")
        if len(field) >= 2:
            hashes[field[0]] = field[1]
    for line in read("/etc/group").splitlines():
        field = line.strip().split(":")
        if len(field) >= 4 and field[0] == GROUP:
            members = set(filter(None, field[3].split(",")))
    links = {link for link in (home.rstrip("/") + "/quartermaster" for _, home, _ in users.values())
             if os.path.lexists(link) or not os.path.isdir(os.path.dirname(link))}
    banner = os.path.exists(BANNER) and not any(os.path.exists(path) for path in USERCONF_FILES)
    return users, hashes, members, read(HASH_FILE).strip(), requested(read(USER_DATA)), links, banner


def main():
    steps = plan(*gather())
    failed = 0
    for step in steps:
        shown = ["<hash>" if step[:2] == ["usermod", "-p"] and index == 2 else part for index, part in enumerate(step)]
        print(" ".join(shown))
        failed += subprocess.run(step, check=False).returncode != 0
    if failed:
        return 1
    os.makedirs(os.path.dirname(STAMP), exist_ok=True)
    with open(STAMP, "w", encoding="utf-8") as handle:
        handle.write("done\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
