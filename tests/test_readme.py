import inspect
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from quartermaster import config, drive_watch, lora
from quartermaster.ui import hat, keys, screens
from quartermaster.ui.square import SquareUI
from tools import build_deb, imager_os_list, pi_release_notes

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
IMAGE_SCRIPT = ROOT / "tools" / "build_image.sh"
VERSION = json.loads((ROOT / "app-builder.json").read_text(encoding="utf-8"))["version"]
REPO = "https://github.com/valleytechsolutions/Quartermaster"
LINK = re.compile(r"\]\(([^)\s]+)\)|(?:href|src|srcset)=\"([^\"]+)\"")
INTO_README = re.compile(re.escape(REPO) + r"(?:/blob/[^/\s)]+/README\.md)?#([\w-]+)")
IMG = re.compile(r"<img\b[^>]*>")
DIY_PICTURE = re.compile(r"docs/screens/diy/(128|240)/([a-z-]+)\.png")
OTHER_FILES = ("*.md", "docs/**/*.md", "docs/**/*.html", "firmware/**/*.md", "tools/*.py", "quartermaster/**/*.py",
               ".github/workflows/*.yml")
PRESS, K1, K2, K3 = 13, 21, 20, 16
USB_TAB, USB_UP, USB_DOWN = 15, 103, 108
WORKFLOWS = ROOT / ".github" / "workflows"
EARLY_PI_RELEASE_TITLES = ("Quartermaster 1.2.5 for Raspberry Pi",)
SCREEN_WORDS = (
    ("Pan details", "quartermaster/ui/square.py", None),
    ("Dismiss alert", "quartermaster/ui/square.py", None),
    ("Quiet 10 min", "quartermaster/ui/square.py", None),
    ("Address and PIN", "quartermaster/ui/square.py", None),
    ("Phone / PC setup", "quartermaster/ui/square.py", None),
    ("Start wardrive", "quartermaster/ui/screens.py", None),
    ("Stop wardrive", "quartermaster/ui/screens.py", None),
    ("Alerts", "quartermaster/ui/screens.py", "A"),
    ("Test", "quartermaster/ui/screens.py", "T"),
    ("Remove", "quartermaster/ui/screens.py", "D"),
    ("Aircraft", "quartermaster/ui/screens.py", "A"),
    ("Start", "quartermaster/ui/recon_screens.py", "Space"),
    ("Save capture", "quartermaster/ui/sky_screens.py", None),
    ("Sort", "quartermaster/ui/sky_screens.py", "Tab"),
    ("Captures", "quartermaster/ui/sky_screens.py", "L"),
    ("Stop + save", "quartermaster/ui/app.py", None),
    ("Stop, don't save", "quartermaster/ui/app.py", None),
)
OPENINGS = (
    ("## What it does", "CardputerZero"),
    ("## Plug in and go", r"\bPi\b"),
    ("## Hardware", r"\bPi\b"),
    ("### Wi-Fi adapters", r"\bPi\b"),
    ("### USB power and Power guard", r"\bPi\b"),
    ("## Nodes", r"\bPi\b"),
    ("## Mesh nodes", "CardputerZero"),
    ("### LoRaWAN networks", "CardputerZero"),
    ("## Settings", r"\bPi\b"),
    ("## Logs", r"\bPi\b"),
    ("## Recon", r"\bPi\b"),
    ("## Drive Alerts", r"\bPi\b"),
    ("## Deep scan", r"\bPi\b"),
)


def read(path):
    return path.read_text(encoding="utf-8")


def section(text, heading):
    """The part of a page from a heading to the next heading of the same level or higher."""
    start = text.index("\n%s\n" % heading) + 1
    level = len(heading.split(" ", 1)[0])
    end = re.compile(r"^#{1,%d} " % level, re.M).search(text, start + len(heading))
    return text[start:end.start() if end else len(text)]


def opening(text, heading):
    """The first paragraph under a heading."""
    return section(text, heading).split("\n\n")[1]


def anchors(text):
    """The anchors GitHub gives the headings of a Markdown page."""
    found, seen, fenced = set(), {}, False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        heading = re.match(r"#{1,6} (.+)", line)
        if fenced or heading is None:
            continue
        slug = re.sub(r"[^\w\- ]", "", heading.group(1).strip().lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        found.add("%s-%d" % (slug, count) if count else slug)
    return found


def columns(table):
    """The cells of each column of the comparison table: the CardputerZero, the 1.44inch HAT and the 1.3inch HAT.
    A cell that spans more than one column belongs to each of them."""
    found = ([], [], [])
    for row in re.findall(r"<tr>(.*?)</tr>", table, re.S):
        column = 0
        for attributes, cell in re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S):
            span = re.search(r"colspan=\"(\d)\"", attributes)
            span = int(span.group(1)) if span else 1
            for index in range(column, column + span):
                found[index].append(cell)
            column += span
    return found


def sentences(cell):
    return re.split(r"(?<=[.!?])\s+", re.sub(r"<[^>]+>", "", cell))


def shell_value(name):
    return re.search(r"\b%s=(\S+)" % name, read(IMAGE_SCRIPT)).group(1)


def imager_names():
    names = []
    for hat_profile in imager_os_list.HATS:
        image = imager_os_list.image_name(hat_profile, VERSION) + ".img.xz"
        meta = {"hat": hat_profile, "version": VERSION, "file": image, "extract_sha256": "ab" * 32,
                "image_download_sha256": "ab" * 32, "extract_size": 1, "image_download_size": 1,
                "release_date": "2026-01-01", "init_format": "systemd"}
        names.append((image, imager_os_list.entry(meta, "owner/repo", "pi-v" + VERSION)["name"]))
    return names


def test_the_readme_says_which_quartermaster_to_get_before_anything_else():
    text = read(README)
    assert text.index("## Which Quartermaster do I need?") < text.index("## User guide") < text.index("## Install")
    which = section(text, "## Which Quartermaster do I need?")
    table = which[which.index("<table>"):which.index("</table>")]
    heads = ["CardputerZero"] + ["DIY with the Waveshare %s" % label for label, _, _ in imager_os_list.HATS.values()]
    assert re.findall(r"<th>([^<]+)</th>", table) == heads
    cardputer, *hats = ("\n".join(cells) for cells in columns(table))
    for words in ('src="docs/screens/home.png"', REPO + "/releases/latest\"", "<code>quartermaster</code>",
                  "WARD", "Cap LoRa-1262", '"for the CardputerZero"'):
        assert words in cardputer, words
    assert "pi-v" not in cardputer
    images = imager_names()
    for index, (column, size) in enumerate(zip(hats, ("128", "240"))):
        assert "<code>%s</code>" % images[index][0] in column and images[1 - index][0] not in column
        for words in ('src="docs/screens/diy/%s/' % size, pi_release_notes.IMAGER[0],
                      REPO + "/releases/tag/pi-v%s\"" % VERSION, "<code>quartermaster-pi</code>", "K1", "K2", "K3",
                      "USB keyboard", "gpsd", '"Raspberry Pi DIY"', 'href="docs/diy.md"'):
            assert words in column, (size, words)
        assert "releases/latest" not in column
    for cell in columns(table)[1]:
        for sentence in sentences(cell):
            if "LoRa" in sentence or "Grove" in sentence:
                assert "CardputerZero" in sentence, sentence
    assert '"for the CardputerZero"' in read(ROOT / "quartermaster/ui/screens.py")
    assert '"Raspberry Pi DIY"' in read(ROOT / "quartermaster/ui/square.py")
    assert "](docs/diy.md#how-the-diy-version-differs-from-the-cardputerzero)" in which


def test_the_readme_says_which_version_it_is_for_and_that_the_two_versions_can_differ():
    text = read(README)
    which = section(text, "## Which Quartermaster do I need?")
    assert "version numbers can differ" in which and "This page is for Quartermaster %s." % VERSION in which
    assert "**About**" in which and "](CHANGELOG.md)" in which
    assert "\n## %s\n" % VERSION in read(ROOT / "CHANGELOG.md")
    assert '"Version %s" % __version__' in read(ROOT / "quartermaster/ui/screens.py")
    assert '"Quartermaster " + __version__' in read(ROOT / "quartermaster/ui/square.py")
    cardputer = section(section(text, "## Install"), "### On a CardputerZero")
    older = [part for part in cardputer.split("\n\n") if "older version than %s" % VERSION in part]
    assert len(older) == 1
    for words in ("AppStore", "one command", "**About**", "**Power guard**", "**Stop, don't save**",
                  "aircraft viewer"):
        assert words in older[0], words
    assert "Power guard" in {label for _, items in config.SECTIONS for _, label, _, _ in items}


def test_the_install_section_keeps_the_two_versions_apart():
    install = section(read(README), "## Install")
    cardputer = section(install, "### On a CardputerZero")
    diy = section(install, "### On a DIY Raspberry Pi")
    one_command = section(cardputer, "#### With one command")
    assert REPO + "/releases/latest/download/quartermaster.deb" in one_command and "Raspberry Pi" in one_command
    for words in ("#### From the CardputerZero AppStore", "**WARD**", "#### After you install",
                  "`sudo apt remove quartermaster`"):
        assert words in cardputer, words
    assert "releases/latest" not in diy
    for image, listed in imager_names():
        assert "`%s`" % image in diy and "**%s**" % listed in diy, image
    manifest = REPO + "/releases/download/pi-v%s/%s)" % (VERSION, pi_release_notes.IMAGER[0])
    for words in ("`pi-v`", "**Latest**", REPO + "/releases/tag/pi-v%s)" % VERSION, manifest,
                  "**Quartermaster %s**" % VERSION, "`passwd`", "`sudo apt remove quartermaster-pi`",
                  "](docs/diy.md#easiest-way-flash-a-ready-image)", "](docs/diy.md#assemble-and-install-manually)"):
        assert words in diy, words
    user, password, host = (shell_value(name) for name in ("login_user", "login_password", "host_name"))
    assert "`ssh %s@%s.local`" % (user, host) in diy and "user `%s` with password `%s`" % (user, password) in diy
    prerelease = "--prerelease" in read(ROOT / ".github" / "workflows" / "release-pi.yml")
    assert ("**Pre-release**" in diy) == prerelease


def test_the_readme_names_every_folder_to_copy_before_a_pi_card_is_flashed_again():
    diy = section(section(read(README), "## Install"), "### On a DIY Raspberry Pi")
    folders = re.search(r"^log_folders=\(([^)]*)\)", read(IMAGE_SCRIPT), re.M).group(1).split()
    named = [paragraph for paragraph in diy.split("\n\n") if "`~/quartermaster/" in paragraph]
    assert len(named) == 1 and set(re.findall(r"`~/quartermaster/([\w-]+)`", named[0])) == set(folders)
    assert "](docs/diy.md#" in named[0]


def test_the_readme_names_the_pi_releases_by_words_that_every_pi_release_ends_in():
    endings = set(re.findall(r'end in "([^"]+)"', read(README)))
    banner = re.search(r'end in \\"([^"\\]+)\\"', read(WORKFLOWS / "release.yml")).group(1)
    assert endings == {banner}
    titles = [title for name in ("release-pi.yml", "promote-pi.yml")
              for title in re.findall(r'--title "([^"]+)"', read(WORKFLOWS / name))]
    assert titles and all(title.endswith(" " + banner) for title in titles + list(EARLY_PI_RELEASE_TITLES))


def test_the_readme_says_a_card_written_from_an_image_file_has_no_wi_fi_yet():
    diy = section(section(read(README), "## Install"), "### On a DIY Raspberry Pi")
    files = diy[diy.index("**The image files.**"):diy.index("**On a card you already use,**")]
    assert "no Wi-Fi network and no Wi-Fi country" in files
    assert "**Flashing an image file yourself**" in files and "](docs/diy.md#good-to-know)" in files
    good = section(read(ROOT / "docs" / "diy.md"), "### Good to know")
    assert "- **Flashing an image file yourself.**" in good and "without your Wi-Fi network or country" in good
    assert 'check "the image sets no Wi-Fi country"' in read(IMAGE_SCRIPT)


def test_the_readme_names_the_button_that_saves_the_keys_on_the_setup_page():
    upload_keys = section(read(README), "## Upload keys")
    assert "press **Save keys**" in upload_keys and "press Save." not in upload_keys
    assert ">Save keys</button>" in read(ROOT / "quartermaster" / "setup_server.py")


def test_the_build_steps_install_the_python_packages_from_the_system():
    build = section(read(README), "## Build it yourself")
    steps = re.search(r"```sh\n(.*?)```", build, re.S).group(1)
    assert "pip install" not in steps
    apt = re.search(r"^sudo apt install (.+)$", steps, re.M).group(1).split()
    python = {"python3-pil", "python3-numpy", "python3-serial"}
    assert python <= {name.split()[0] for name in build_deb.DEPENDS.split(", ")}
    assert python | {"python3-pytest", "git"} <= set(apt)
    assert steps.index("sudo apt install") < steps.index("git clone") < steps.index("python3 -m pytest")


def test_every_link_and_picture_in_the_readme_leads_somewhere():
    text = read(README)
    broken = []
    for match in LINK.finditer(text):
        target = match.group(1) or match.group(2)
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        linked = (ROOT / path).resolve() if path else README
        if not linked.exists():
            broken.append(target)
        elif anchor and linked.suffix == ".md" and anchor not in anchors(read(linked)):
            broken.append(target)
    assert not broken


def test_links_from_other_files_into_the_readme_find_their_heading():
    headings = anchors(read(README))
    pages = {page for pattern in OTHER_FILES for page in ROOT.glob(pattern)} - {README}
    missing = []
    for page in sorted(pages):
        text = read(page)
        for anchor in INTO_README.findall(text):
            if anchor not in headings:
                missing.append("%s: #%s" % (page.relative_to(ROOT), anchor))
        if page.suffix not in (".md", ".html"):
            continue
        for match in LINK.finditer(text):
            path, _, anchor = (match.group(1) or match.group(2)).partition("#")
            if path and anchor and "://" not in path and (page.parent / path).resolve() == README and \
                    anchor not in headings:
                missing.append("%s: #%s" % (page.relative_to(ROOT), anchor))
    assert not missing


def test_the_diy_pictures_in_the_readme_come_in_pairs_for_both_hats():
    text = read(README)
    hats = {key.rsplit("-", 1)[1]: label.split()[0] for key, (label, _, _) in imager_os_list.HATS.items()}
    found = []
    for tag in IMG.findall(text):
        picture = DIY_PICTURE.search(tag)
        if picture is None:
            continue
        size, name = picture.groups()
        alt = re.search(r"\balt=\"([^\"]+)\"", tag)
        assert (ROOT / "docs" / "screens" / "diy" / size / (name + ".png")).is_file(), tag
        assert alt and hats[size] in alt.group(1), tag
        found.append((size, name))
    assert found and len(found) == len(DIY_PICTURE.findall(text))
    assert [name for size, name in found if size == "128"] == [name for size, name in found if size == "240"]
    shots = section(text, "## Screens")
    assert shots.index("### CardputerZero\n") < shots.index("### DIY Raspberry Pi\n")
    gallery = section(shots, "### DIY Raspberry Pi")
    shown = [name for size, name in DIY_PICTURE.findall(gallery) if size == "128"]
    captions = re.findall(r"<td align=\"center\">(.+?)</td>", gallery)
    assert len(shown) == len(set(shown)) == len(captions) >= 6
    assert "](docs/diy.md#screens)" in gallery


def test_each_row_of_diy_pictures_in_the_readme_is_headed_by_its_own_hat():
    gallery = section(section(read(README), "## Screens"), "### DIY Raspberry Pi")
    order = [key.rsplit("-", 1)[1] for key in imager_os_list.HATS]
    hats = dict(zip(order, imager_os_list.HATS.values()))
    pictured = []
    for row in re.findall(r"<tr>(.*?)</tr>", gallery, re.S):
        pictures = DIY_PICTURE.findall(row)
        if not pictures:
            continue
        sizes = {size for size, _ in pictures}
        assert len(sizes) == 1, row
        size = sizes.pop()
        header = re.match(r"\s*<th>([^<]+)<br>HAT</th>", row)
        assert header and header.group(1) + " LCD HAT" == hats[size][0], row
        pictured.append((size, [name for _, name in pictures]))
    assert pictured and [size for size, _ in pictured] == order * (len(pictured) // len(order))
    for start in range(0, len(pictured), len(order)):
        assert len({tuple(names) for _, names in pictured[start:start + len(order)]}) == 1, pictured[start]
    top, under = (hats[size] for size in order)
    assert "the top picture is the Waveshare %s (%s) and the one under it the Waveshare %s (%s)." % (
        top[0], top[1], under[0], under[1]) in gallery
    diy_screens = section(read(ROOT / "docs" / "diy.md"), "## Screens")
    assert "shows the main DIY screens" in gallery and "show the main DIY screens" in diy_screens


def test_the_diy_controls_in_the_readme_match_the_buttons_of_the_hat():
    assert hat.BUTTONS[PRESS] == hat.BUTTONS[K1] == "ENTER"
    assert hat.BUTTONS[K2] == "ESC"
    assert hat.BUTTONS[K3] == keys.CODES[USB_TAB] == "TAB"
    controls = section(read(README), "## Controls")
    assert controls.index("### CardputerZero keys") < controls.index("### DIY Raspberry Pi controls")
    diy = section(controls, "### DIY Raspberry Pi controls")
    rows = re.findall(r"^\| (.+?) \| (.+?) \|$", diy, re.M)

    def does(button):
        return " ".join(what for name, what in rows if button in name)
    assert all(way in does("joystick").lower() for way in ("up", "down", "left", "right"))
    assert "**OK**" in does("**K1**") and "**OK**" in does("Press the joystick")
    assert "**Esc**" in does("**K2**")
    assert "actions" in does("**K3**") and "keyboard" in does("**K3**")
    for char in ("⌫", "✓"):
        assert char in SquareUI.characters() and "**%s**" % char in diy, char
    for words in ("**Pan details**", "USB keyboard", "**Tab**", "](docs/diy.md#where-the-cardputerzero-keys-are)"):
        assert words in diy, words


def test_the_readme_says_how_a_usb_keyboard_on_a_pi_differs():
    usb = [line for line in section(read(README), "### DIY Raspberry Pi controls").splitlines()
           if line.startswith("- **A USB keyboard**")]
    assert len(usb) == 1
    for words in ("**Tab** and the keys for up and down work differently", "**Tab**", "on **F** and **X**",
                  '"Stop the wardrive?"',
                  "before you switch the Pi on"):
        assert words in usb[0], words
    assert '"Stop the wardrive?"' in read(ROOT / "quartermaster/ui/app.py")
    assert keys.CODES[USB_UP] == "UP" and keys.CODES[USB_DOWN] == "DOWN"

    def question():
        return screens.Confirm("Stop the wardrive?", "", "Stop + save", lambda: None, default=1,
                               extra=("Stop, don't save", lambda: None, ""))
    cardputer, pi = question(), question()
    cardputer.handle(keys.Key("DOWN"), None)
    assert cardputer.choice == 2
    assert SquareUI(128).handle(keys.Key("DOWN"), SimpleNamespace(screen=pi)) and pi.choice == 1
    assert inspect.getsource(keys.EvdevInput).count("find_keyboards()") == 1
    assert "find_keyboards()" in inspect.getsource(keys.EvdevInput.__init__)
    assert "EvdevInput()" in inspect.getsource(hat.HatInput.__init__)
    assert "EvdevInput(" not in inspect.getsource(hat.HatInput.poll)


@pytest.mark.parametrize("label, source, key", SCREEN_WORDS)
def test_the_pi_words_in_the_readme_are_the_words_on_the_screen(label, source, key):
    assert "**%s**" % label in read(README)
    code = read(ROOT / source)
    assert '"%s"' % label in code
    if key:
        assert re.search(r"\(\s*\"%s\"\s*,\s*\"%s\"\s*\)" % (re.escape(key), re.escape(label)), code)


def test_what_the_readme_says_a_pi_lacks_is_what_the_pi_leaves_out():
    text = read(README)
    entries = {key: (label, choices) for _, items in config.SECTIONS for key, label, choices, _ in items}
    settings = opening(text, "## Settings")
    for words in (entries["lora"][0], entries["gps_power"][0], dict(entries["gps_source"][1])["/dev/ttyS0"],
                  drive_watch.NAMES["lorawan"]):
        assert "**%s**" % words in settings, words
    table = section(text, "## Settings")
    for key in ("lora", "gps_power", "gps_source"):
        row = re.search(r"^\|[^|]*\|\s*%s\s*\|.*$" % re.escape(entries[key][0]), table, re.M)
        assert row and "CardputerZero" in row.group(0), key
    mesh = opening(text, "## Mesh nodes")
    assert "CardputerZero" in mesh and "**%s**" % entries["lora"][0] in mesh
    pi = {key: choices for key, _, choices, _ in screens.Settings(diy=True).items}
    cardputer = {key for key, _, _, _ in screens.Settings().items}
    assert {"lora", "gps_power"} <= cardputer and not {"lora", "gps_power"} & set(pi)
    assert "/dev/ttyS0" not in [value for value, _ in pi["gps_source"]]
    assert screens.supported_alerts(SimpleNamespace(square=object()), ["flock", "lorawan"]) == ["flock"]
    assert lora.Cap(cardputer=False).state == lora.ABSENT


@pytest.mark.parametrize("heading, version", OPENINGS)
def test_a_section_that_differs_between_the_versions_says_so_at_its_start(heading, version):
    assert re.search(version, opening(read(README), heading)), heading


def test_the_readme_uses_no_long_dashes():
    assert "—" not in read(README)
