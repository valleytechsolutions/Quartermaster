import json
from pathlib import Path
import re

import pytest

from quartermaster import adsb, flasher, nodes, radio_helper
from quartermaster.ui import hat, screens
from tests.test_pi_words import HARDWARE, LISTS, PAGES, PHONE, UPLOADS
from tools import imager_os_list

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "guide"
VERSION = json.loads((ROOT / "app-builder.json").read_text(encoding="utf-8"))["version"]
DIY = "## On a DIY Raspberry Pi"
DIY_PAGES = ("install", "first-start", "wardrive", "settings", "files", "uploads", "alerts", "aircraft-and-lora",
             "power", "troubleshooting", "wifi-adapters", "deep-scan", "more-networks", "nodes", "biscuit")
LINK = re.compile(r"\]\(([^)\s]+)\)|<img src=\"([^\"]+)\"")
PICTURE = re.compile(r"<img src=\"\.\./screens/diy/(128|240)/([a-z-]+)\.png\" width=\"(\d+)\" alt=\"[^\"]+\">")
SCREEN_WORDS = {
    "quartermaster/ui/square.py": ("Phone / PC setup", "Address and PIN", "Pan details", "Dismiss alert",
                                   "Quiet 10 min", "Connect the Pi and phone to the same network.",
                                   "Stop the wardrive to change this.", "Raspberry Pi DIY"),
    "quartermaster/ui/screens.py": ("Plug in a USB GPS or connect a receiver through gpsd.", "Check adapter",
                                    "GPS searching", "No fix yet", "Start wardrive", "Stop wardrive",
                                    "New wardrive", "Upload again", "Test keys", "Radio for aircraft (RTL-SDR)",
                                    "K3: Aircraft", "Open Keys on the menu to add your upload keys.",
                                    "Automatic uploads are off. Open Uploads to send it.",
                                    "GPS fix lost. Logging pauses until it is back."),
    "quartermaster/radio_helper.py": ("Radio helper not found. Reinstall the Pi package from GitHub.",
                                      "Restart the Pi once so the new permission applies."),
    "quartermaster/adsb.py": ("No permission yet. Restart the Pi once.", "Restart the Pi once to use it"),
    "quartermaster/ui/sky_screens.py": ("Save capture", "End capture", "Captures"),
    "quartermaster/ui/recon_screens.py": ("Reset peak", "All on", "Watch list", "Mute", "Play", "Defaults",
                                          "No alerts"),
    "quartermaster/ui/app.py": ("Open Keys to add upload keys",),
    "quartermaster/powerguard.py": ("The Pi reports under-voltage", "Estimate. Within what the Pi's USB ports give."),
    "quartermaster/updates_check.py": ("X is ready. Install the Pi package from GitHub.",
                                       "Your Pi version (X) is up to date.",
                                       "Could not find a Raspberry Pi release with an installer."),
}


def pages():
    return sorted(GUIDE.glob("*.md"))


def read(path):
    return path.read_text(encoding="utf-8")


def diy_part(text):
    return text.split("\n%s\n" % DIY, 1)[1] if "\n%s\n" % DIY in text else ""


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


def test_the_index_says_which_device_it_is_written_for_and_points_to_the_diy_version():
    text = read(GUIDE / "README.md")
    assert "Quartermaster %s on the M5Stack CardputerZero" % VERSION in text
    assert "](../diy.md)" in text
    for words in ("**On a DIY Raspberry Pi**", "`quartermaster-pi`", "quartermaster-pi_%s_all.deb" % VERSION,
                  "**Latest**", "releases/tag/pi-v%s" % VERSION, "**K1**", "**K2**", "**K3**"):
        assert words in text, words


@pytest.mark.parametrize("name", DIY_PAGES)
def test_each_page_that_differs_ends_with_one_diy_section(name):
    text = read(GUIDE / (name + ".md"))
    assert text.count("\n%s\n" % DIY) == 1
    assert "\n## " not in diy_part(text)
    assert "[Back to the guide](README.md)" in text


def test_pi_links_name_this_pi_release_and_never_the_latest_release():
    for page in pages():
        text = read(page)
        assert set(re.findall(r"pi-v(\d+\.\d+\.\d+)", text)) <= {VERSION}, page.name
        assert set(re.findall(r"quartermaster-pi[_-](\d+\.\d+\.\d+)", text)) <= {VERSION}, page.name
        assert set(re.findall(r"releases/latest\S*", text)) <= {"releases/latest/download/quartermaster.deb"}
        assert "releases/latest" not in diy_part(text), page.name


def test_the_install_page_names_both_pi_images_as_imager_shows_them():
    text = diy_part(read(GUIDE / "install.md"))
    for hat_profile in imager_os_list.HATS:
        image = imager_os_list.image_name(hat_profile, VERSION) + ".img.xz"
        meta = {"hat": hat_profile, "version": VERSION, "file": image, "extract_sha256": "ab" * 32,
                "image_download_sha256": "ab" * 32, "extract_size": 1, "image_download_size": 1,
                "release_date": "2026-01-01", "init_format": "systemd"}
        assert "`%s`" % image in text
        assert '"%s"' % imager_os_list.entry(meta, "owner/repo", "pi-v" + VERSION)["name"] in text
    assert "`quartermaster.rpi-imager-manifest`" in text
    assert "sudo apt remove quartermaster-pi\n" in text


def test_every_link_and_picture_in_the_guide_leads_somewhere():
    broken = []
    for page in pages():
        for match in LINK.finditer(read(page)):
            target = match.group(1) or match.group(2)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path, _, anchor = target.partition("#")
            linked = (page.parent / path).resolve() if path else page
            if not linked.exists():
                broken.append("%s: %s" % (page.name, target))
            elif anchor and linked.suffix == ".md" and anchor not in anchors(read(linked)):
                broken.append("%s: %s" % (page.name, target))
    assert not broken


def test_diy_pictures_come_in_pairs_for_both_hats_at_their_sizes():
    for page in pages():
        text = read(page)
        found = PICTURE.findall(text)
        assert text.count("screens/diy/") == len(found), page.name
        for size, _, width in found:
            assert width == {"128": "256", "240": "240"}[size], page.name
        assert [name for size, name, _ in found if size == "128"] == \
               [name for size, name, _ in found if size == "240"], page.name
        if page.name != "README.md":
            assert diy_part(text).count("screens/diy/") == len(found), page.name


def test_the_pi_words_in_the_guide_are_the_words_on_the_screen():
    guide = "\n".join(read(page) for page in pages())
    for source, phrases in SCREEN_WORDS.items():
        code = read(ROOT / source)
        for phrase in phrases:
            assert phrase in guide, phrase
            assert phrase.replace("X", "%s") in code, phrase


def test_a_page_that_quotes_a_message_a_pi_words_differently_gives_the_pi_words_in_its_diy_part():
    pairs = UPLOADS + PHONE + PAGES + HARDWARE + LISTS
    for page in pages():
        text = read(page)
        diy = diy_part(text)
        for cardputer, pi in pairs:
            assert cardputer not in diy, (page.name, cardputer)
            if re.search(re.escape(cardputer) + r"(?=\"| \|)", text[:len(text) - len(diy)]):
                assert pi in diy, (page.name, pi)
    guide = read(ROOT / "docs" / "diy.md")
    assert [cardputer for cardputer, _ in pairs if cardputer in guide] == []


def test_the_messages_that_ask_a_pi_to_restart_update_or_reinstall_are_ones_a_pi_shows():
    shown = [*radio_helper.PI_WORDS.values(), *adsb.PI_WORDS.values(), nodes.NEWER, nodes.NO_SERIAL, flasher.NEWER,
             flasher.NO_SERIAL, flasher.DAMAGED]
    trouble = diy_part(read(GUIDE / "troubleshooting.md"))
    table = trouble.split("**Messages that ask you to restart, update or reinstall.**", 1)[1].split("\n\n**", 1)[0]
    rows = re.findall(r"^\| (.+?) \| .+ \|$", table, re.M)
    assert rows[:2] == ["Where a message says", "---"] and len(rows) == 6
    diy = read(ROOT / "docs" / "diy.md")
    asks = next(line for line in diy.splitlines() if line.startswith("**Messages that ask you to restart"))
    quoted = re.findall(r"\"([^\"]+)\"", asks)
    assert len(quoted) == 4 and "`sudo reboot`" in asks and "](guide/troubleshooting.md#on-a-diy-raspberry-pi)" in asks
    for words in rows[2:] + quoted:
        assert any(words in message for message in shown), words
    assert not re.search(r"Cardputer|AppStore", table + asks)


def test_the_pi_keys_in_the_guide_match_the_buttons_of_the_hat():
    assert hat.BUTTONS[21] == hat.BUTTONS[13] == "ENTER"
    assert hat.BUTTONS[20] == "ESC"
    assert hat.BUTTONS[16] == "TAB"
    keys = read(GUIDE / "wardrive.md")
    assert "| **OK** (Enter) | Press the joystick, or press **K1** |" in keys
    assert "| **Esc** | **K2** |" in keys
    usb = diy_part(keys).split("A USB keyboard works on the Pi too", 1)[1].split("\n", 1)[0]
    for words in ("**Tab** and the keys for up and down work differently", "**Tab** does what **K3** does.",
                  "on the arrow keys or on **F** and **X**", "on the menu they move one item at a time",
                  "only left and right change the choice",
                  "Plug the keyboard in before you switch the Pi on"):
        assert words in usb, words
    assert "Only **Tab**" not in keys


def test_the_index_says_a_pi_has_nearly_the_same_settings():
    cardputer, pi = screens.Settings(), screens.Settings(diy=True)
    missing = {key for key, _, _, _ in cardputer.items if key} - {key for key, _, _, _ in pi.items if key}
    assert missing == {"lora", "gps_power"}
    assert "same screens and nearly the same settings" in read(GUIDE / "README.md")
    settings = diy_part(read(GUIDE / "settings.md"))
    for key, label, _, _ in cardputer.items:
        if key in missing:
            assert "**%s**" % label in settings, label


def test_the_install_page_names_the_pi_release_as_github_shows_it():
    workflow = read(ROOT / ".github" / "workflows" / "release-pi.yml")
    assert '--title "Quartermaster $VERSION DIY for Raspberry Pi"' in workflow and "--prerelease" in workflow
    text = diy_part(read(GUIDE / "install.md"))
    assert ("The release [Quartermaster %s DIY for Raspberry Pi](https://github.com/valleytechsolutions/Quartermaster/"
            "releases/tag/pi-v%s)" % (VERSION, VERSION)) in text
    assert "A new Pi release is marked **Pre-release** until it has been tested." in text
    for page in pages():
        assert "Quartermaster Pi %s release" % VERSION not in read(page), page.name


def test_updating_a_pi_points_to_the_steps_that_keep_one_copy_of_quartermaster():
    diy = read(ROOT / "docs" / "diy.md")
    for name in ("**Updating.**", "**Flashing the newest image.**", "Skip steps 4 and 5"):
        assert name in diy, name
    install = diy_part(read(GUIDE / "install.md"))
    assert "**Updating** and **Flashing the newest image** under [Good to know](../diy.md#good-to-know)" in install
    assert "copy the `logs`, `recon` and `aircraft` folders" in install
    trouble = diy_part(read(GUIDE / "troubleshooting.md"))
    assert "**Updating** under [Good to know](../diy.md#good-to-know)" in trouble
    assert "Run only step 3 of the manual install" in trouble


def test_the_pi_commands_for_the_radio_helper_and_a_reinstall_do_what_they_say():
    postinst = read(ROOT / "packaging" / "postinst")
    assert "systemctl enable --now quartermaster-radio.socket" in postinst
    command = "`sudo systemctl enable --now quartermaster-radio.socket`"
    for name in ("troubleshooting", "deep-scan"):
        text = diy_part(read(GUIDE / (name + ".md")))
        assert command in text, name
        assert "install the Pi package again" not in text, name
    trouble = diy_part(read(GUIDE / "troubleshooting.md"))
    assert ("`sha256sum --check SHA256SUMS && sudo apt install --reinstall ./quartermaster-pi_%s_all.deb`"
            % VERSION) in trouble
    manual = read(ROOT / "docs" / "diy.md").split("\n## Assemble and install manually\n", 1)[1]
    step3 = manual.split("\n3. ", 1)[1].split("\n4. ", 1)[0]
    assert "sha256sum --check SHA256SUMS && sudo apt install ./quartermaster-pi_%s_all.deb\n" % VERSION in step3


def test_the_guide_uses_no_long_dashes():
    for page in pages():
        assert "—" not in read(page), page.name
