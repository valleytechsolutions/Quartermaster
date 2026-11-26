import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from tools import pi_release_notes

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
NEW = ["quartermaster-pi-9.9.9-waveshare-1.44inch-lcd-hat.img.xz", "quartermaster-pi-9.9.9-waveshare-1.3inch-lcd-hat.img.xz"]
OLD = ["quartermaster-pi-9.9.9-st7735-128.img.xz", "quartermaster-pi-9.9.9-st7789-240.img.xz"]
REST = ["quartermaster-pi_9.9.9_all.deb", "SHA256SUMS", "quartermaster-imager.json", "quartermaster.rpi-imager-manifest"]
SCREENS = [pi_release_notes.screen(hat, name)
           for hat, *_ in pi_release_notes.HATS for name, _ in pi_release_notes.GALLERY]
EVERY_PICTURE = [*SCREENS, pi_release_notes.PICTURE]

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


def held(images):
    return {*images, *(name + ".sha256" for name in images), *REST}


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                           "-c", "tag.gpgsign=false", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(root, files, message="pictures"):
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        git("init", "-q", cwd=root)
    for path in files:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"\x89PNG")
    git("add", "--all", cwd=root)
    git("commit", "-q", "--allow-empty", "-m", message, cwd=root)
    return git("rev-parse", "HEAD", cwd=root)


@pytest.fixture
def ref(tmp_path, monkeypatch):
    """A commit of the checkout that has every picture the notes can show."""
    monkeypatch.setattr(pi_release_notes, "ROOT", tmp_path / "checkout")
    return commit(tmp_path / "checkout", EVERY_PICTURE)


@needs_git
def test_the_notes_say_which_version_they_are_for_and_show_each_hat_with_its_image(ref):
    text = pi_release_notes.notes("owner/repo", "pi-v9.9.9", ref)
    first = text.splitlines()[0]
    assert first.startswith("**This is the DIY version of Quartermaster**, for a Raspberry Pi with a Waveshare")
    assert "M5Stack CardputerZero version" in first and "(https://github.com/owner/repo/releases/latest)" in first
    for (hat, label, pixels, chip), name in zip(pi_release_notes.HATS, NEW):
        row = next(line for line in text.splitlines() if "<br>**Waveshare %s**<br>" % label in line)
        assert "<img src='https://www.waveshare.com/media/catalog/product/1/_/%s-1.jpg'" % pi_release_notes.page(label) in row
        assert "<sub>%s screen, %s</sub>" % (pixels, chip) in row and row.endswith("| `%s` |" % name)
    assert text.index("| Your HAT | Its image |") < text.index("Photos by Waveshare.") < \
        text.index("### What it looks like") < text.index("### Flash a ready SD card") < \
        text.index("### Log in and controls") < text.index("### What is new") < \
        text.index("The [DIY build and installation guide]")
    for words in ("Download `quartermaster.rpi-imager-manifest` below and open it", "then **Quartermaster 9.9.9**",
                  "can only join a 2.4 GHz one", "On **Localisation**, choose your capital city",
                  "the channels of your country. Then write the card.",
                  "5. With the Pi off, put the HAT on the 40-pin header and plug in the GPS.",
                  "On a Zero, use the port marked **USB** for the GPS and **PWR IN** for power.",
                  "6. Put the card in the Pi and switch it on. The first start takes a few minutes, and the Pi may"
                  " restart once.",
                  "download the image for your HAT under **Assets** below. The table above gives its name.",
                  "**Use custom**", "user `pi` with password `pi`", "`ssh pi@quartermaster.local`",
                  "Change the password with `passwd`",
                  "A user set in Raspberry Pi Imager replaces this login, so then log in as that user.",
                  "A hostname set there replaces `quartermaster`.",
                  "press **K1** to select. **K2** goes back, and **K3**", "`~/quartermaster/logs`",
                  "(https://github.com/owner/repo/blob/pi-v9.9.9/docs/diy.md)",
                  "The [changelog](https://github.com/owner/repo/blob/pi-v9.9.9/CHANGELOG.md#999) lists what is new"
                  " in 9.9.9.",
                  "To update a card you already use, see **Updating** under"
                  " [Good to know](https://github.com/owner/repo/blob/pi-v9.9.9/docs/diy.md#good-to-know) in the guide.",
                  "The images are added to this release shortly after it is published.",
                  "While this is the newest Pi release, its images are rebuilt every month"):
        assert words in text, words
    assert "for your HAT above" not in text
    assert "—" not in text and not any(name in text for name in OLD)


@needs_git
@pytest.mark.parametrize("tag, all_folders", [("pi-v1.2.8", False), ("pi-v1.2.9", True), ("pi-v1.2.10", True),
                                              ("pi-v1.10.0", True), ("pi-v2.0.0", True), ("pi-v0.9.9", False)])
def test_the_notes_name_the_folders_that_the_image_of_their_version_lets_the_login_read(ref, tag, all_folders):
    text = pi_release_notes.notes("o/r", tag, ref)
    line = next(line for line in text.splitlines() if line.startswith("- Saved drives are in "))
    if all_folders:
        assert line == ("- Saved drives are in `~/quartermaster/logs`, Recon and Drive Alert logs in"
                        " `~/quartermaster/recon` and aircraft captures in `~/quartermaster/aircraft`.")
    else:
        assert line == "- Saved drives are in `~/quartermaster/logs`."
    assert "log_folders=(logs recon aircraft)" in (ROOT / "tools" / "build_image.sh").read_text()
    version = json.loads((ROOT / "app-builder.json").read_text())["version"]
    assert tuple(int(part) for part in version.split(".")) >= pi_release_notes.ALL_FOLDERS_FROM


def anchor(heading):
    """The link GitHub gives a heading of a Markdown file."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


@needs_git
@pytest.mark.parametrize("version", ["9.9.9", "1.2.10"])
def test_the_notes_link_the_changes_of_their_version_and_the_updating_steps_at_the_tag(ref, version):
    text = pi_release_notes.notes("o/r", "pi-v%s" % version, ref)
    part = text[text.index("### What is new"):text.index("The [DIY build and installation guide]")]
    links = re.findall(r"\[([^]]+)\]\(([^)]+)\)", part)
    assert links == [("changelog", "https://github.com/o/r/blob/pi-v%s/CHANGELOG.md#%s" % (version, anchor(version))),
                     ("Good to know", "https://github.com/o/r/blob/pi-v%s/docs/diy.md#good-to-know" % version)]
    assert "what is new in %s." % version in part and "see **Updating** under" in part


def test_the_changes_and_updating_links_lead_to_a_heading_of_this_version():
    version = json.loads((ROOT / "app-builder.json").read_text())["version"]
    headings = [line[3:] for line in (ROOT / "CHANGELOG.md").read_text().splitlines() if line.startswith("## ")]
    assert version in headings and anchor(version) == version.replace(".", "")
    guide = (ROOT / "docs" / "diy.md").read_text()
    good = guide.index("\n### Good to know\n")
    assert "\n- **Updating.**" in guide[good:guide.index("\n## ", good)] and anchor("Good to know") == "good-to-know"


def looks(text):
    return text[text.index("### What it looks like"):text.index("### Flash a ready SD card")]


@needs_git
def test_the_notes_show_the_diy_screens_on_both_hats_from_the_commit(ref):
    text = pi_release_notes.notes("owner/repo", "pi-v9.9.9", ref)
    shown = looks(text)
    assert "Here is how the menu, a wardrive, the Hardware screen and the aircraft viewer look on each HAT." in shown
    lines = shown.splitlines()
    for (hat, label, pixels, chip), folder in zip(pi_release_notes.HATS, ("128", "240")):
        row = lines[lines.index("**Waveshare %s**, %s screen" % (label, pixels)) + 2]
        pictures = re.findall(r"<img src='([^']+)' width='200' alt='([^']+)'>", row)
        wanted = ["Quartermaster on the Waveshare %s: %s" % (label, what)
                  for what in ("the menu", "a wardrive", "the Hardware screen", "the aircraft viewer")]
        assert [alt for _, alt in pictures] == wanted
        for (url, _), name in zip(pictures, ("menu", "wardrive", "hardware", "aircraft")):
            path = "docs/screens/diy/%s/%s.png" % (folder, name)
            assert url == "https://raw.githubusercontent.com/owner/repo/%s/%s" % (ref, path)
            assert (ROOT / path).is_file(), path
    assert shown.index("**Waveshare 1.44inch LCD HAT**") < shown.index("**Waveshare 1.3inch LCD HAT**")
    assert "waveshare-preview.png" not in text and "—" not in shown


def older_picture(text, ref):
    return looks(text).startswith(
        "### What it looks like\n\n<img src='https://raw.githubusercontent.com/owner/repo/%s/"
        "docs/screens/waveshare-preview.png' width='520' alt='Quartermaster on both HATs:" % ref) and \
        "docs/screens/diy" not in text and "Here is how" not in text


@needs_git
@pytest.mark.parametrize("screens", [SCREENS[1:], SCREENS[:-1], []])
def test_a_commit_without_every_diy_picture_shows_the_older_picture_of_both_hats(tmp_path, monkeypatch, screens):
    monkeypatch.setattr(pi_release_notes, "ROOT", tmp_path)
    ref = commit(tmp_path, [*screens, pi_release_notes.PICTURE])
    assert older_picture(pi_release_notes.notes("owner/repo", "pi-v9.9.9", ref), ref)


@needs_git
def test_the_pictures_are_taken_from_the_commit_and_not_from_the_files_of_the_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(pi_release_notes, "ROOT", tmp_path)
    tagged = commit(tmp_path, [pi_release_notes.PICTURE])
    newer = commit(tmp_path, SCREENS)
    assert all((tmp_path / path).is_file() for path in EVERY_PICTURE)
    text = pi_release_notes.notes("owner/repo", "pi-v9.9.9", tagged)
    assert older_picture(text, tagged) and newer not in text
    text = pi_release_notes.notes("owner/repo", "pi-v9.9.9", newer)
    assert "/%s/docs/screens/diy/128/menu.png' width='200'" % newer in looks(text) and tagged not in text
    alone = tmp_path / "untracked"
    monkeypatch.setattr(pi_release_notes, "ROOT", alone)
    only = commit(alone, [pi_release_notes.PICTURE])
    for path in SCREENS:
        (alone / path).parent.mkdir(parents=True, exist_ok=True)
        (alone / path).write_bytes(b"\x89PNG")
    assert older_picture(pi_release_notes.notes("owner/repo", "pi-v9.9.9", only), only)


@needs_git
def test_the_notes_need_a_picture_of_the_screens(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pi_release_notes, "ROOT", tmp_path / "none")
    ref = commit(tmp_path / "none", SCREENS[1:])
    with pytest.raises(ValueError, match="has neither the pictures in docs/screens/diy nor docs/screens/waveshare"):
        pi_release_notes.notes("o/r", "pi-v9.9.9", ref)
    with pytest.raises(SystemExit):
        pi_release_notes.main(["--repo", "o/r", "--tag", "pi-v9.9.9", "--ref", ref])
    assert "has neither the pictures" in capsys.readouterr().err
    monkeypatch.setattr(pi_release_notes, "ROOT", tmp_path / "diy")
    ref = commit(tmp_path / "diy", SCREENS)
    assert pi_release_notes.main(["--repo", "o/r", "--tag", "pi-v9.9.9", "--ref", ref]) == 0
    assert "/docs/screens/diy/240/aircraft.png' width='200'" in capsys.readouterr().out


@needs_git
def test_the_notes_need_a_commit_that_this_checkout_has(ref, tmp_path, capsys):
    tree = git("rev-parse", ref + "^{tree}", cwd=tmp_path / "checkout")
    for missing in ("0" * 40, "0123456789abcdef0123456789abcdef01234567", tree):
        with pytest.raises(ValueError, match="this checkout has no commit %s" % missing):
            pi_release_notes.notes("o/r", "pi-v9.9.9", missing)
    with pytest.raises(SystemExit):
        pi_release_notes.main(["--repo", "o/r", "--tag", "pi-v9.9.9", "--ref", "0" * 40])
    assert "this checkout has no commit" in capsys.readouterr().err


@needs_git
def test_the_notes_of_a_release_name_the_images_it_holds(ref):
    for images in (NEW, OLD):
        text = pi_release_notes.notes("o/r", "pi-v9.9.9", ref, held(images))
        assert all("`%s`" % name in text for name in images)
        assert "shortly after it is published" not in text
    text = pi_release_notes.notes("o/r", "pi-v9.9.9", ref, held(NEW) | held(OLD))
    assert all("`%s`" % name in text for name in NEW) and not any(name in text for name in OLD)


@needs_git
@pytest.mark.parametrize("assets, message", [
    (held(NEW) - {NEW[0]}, "no image with its .sha256 for the Waveshare 1.44inch LCD HAT"),
    (held(OLD) - {OLD[1] + ".sha256"}, "no image with its .sha256 for the Waveshare 1.3inch LCD HAT"),
    (held(NEW) - {"quartermaster.rpi-imager-manifest"}, "no quartermaster.rpi-imager-manifest"),
    (held(NEW) - {"quartermaster-imager.json"}, "no quartermaster-imager.json"),
    (set(REST), "no image"),
])
def test_the_notes_refuse_a_release_without_its_images_or_imager_files(ref, assets, message):
    with pytest.raises(ValueError, match=message):
        pi_release_notes.notes("o/r", "pi-v9.9.9", ref, assets)


@needs_git
def test_the_notes_need_a_pi_tag_and_a_commit(ref, tmp_path, capsys):
    for tag in ("v9.9.9", "pi-v9.9", "pi-v9.9.9-rc1"):
        with pytest.raises(ValueError, match="not a Pi release tag"):
            pi_release_notes.notes("o/r", tag, ref)
    for wrong in ("main", ref[:12], ref.upper()):
        with pytest.raises(ValueError, match="not a commit"):
            pi_release_notes.notes("o/r", "pi-v9.9.9", wrong)
    assets = tmp_path / "assets.txt"
    assets.write_text("\n".join(sorted(held(OLD))) + "\n\n")
    assert pi_release_notes.main(["--repo", "o/r", "--tag", "pi-v9.9.9", "--ref", ref, "--assets", str(assets)]) == 0
    assert capsys.readouterr().out == pi_release_notes.notes("o/r", "pi-v9.9.9", ref, held(OLD))
    for wrong in (["--tag", "v9.9.9", "--ref", ref], ["--tag", "pi-v9.9.9", "--ref", "main"], ["--ref", ref]):
        with pytest.raises(SystemExit):
            pi_release_notes.main(["--repo", "o/r", *wrong])
    capsys.readouterr()
    assert (ROOT / pi_release_notes.PICTURE).is_file()


def test_a_pi_release_becomes_a_full_release_only_by_hand_and_never_takes_latest():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((WORKFLOWS / "promote-pi.yml").read_text())
    assert set(workflow[True]) == {"workflow_dispatch"}
    assert workflow[True]["workflow_dispatch"]["inputs"]["tag"]["required"] is True
    assert workflow["permissions"] == {"contents": "write"}
    assert workflow["concurrency"] == {"group": "release-${{ inputs.tag }}"}
    release = yaml.safe_load((WORKFLOWS / "release-pi.yml").read_text())
    assert release["concurrency"]["group"].startswith("release-${{ inputs.tag")
    steps = workflow["jobs"]["promote"]["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@") and "with" not in steps[0]
    write = next(step["run"] for step in steps if step.get("name") == "Write the release notes")
    fetch = 'git fetch --no-tags --depth=1 origin "refs/tags/$TAG"\n'
    assert write.index(fetch) < write.index("tagged=$(git rev-parse 'FETCH_HEAD^{commit}')\n") < \
        write.index('tools/pi_release_notes.py --repo "$GITHUB_REPOSITORY" --tag "$TAG" --ref "$tagged"')
    assert "rev-parse HEAD" not in write and "--assets build/assets.txt" in write
    edit = steps[-1]["run"]
    assert 'gh release edit "$TAG" --prerelease=false --latest=false' in edit
    assert edit.count("gh release edit") == 2 and 'gh release edit "$LATEST" --latest\n' in edit


FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_GH"]
state = json.load(open(path))
args = sys.argv[1:]
state["calls"].append(args)
if args[:2] == ["release", "view"]:
    tag = args[2] if len(args) > 2 and not args[2].startswith("-") else state["latest"]
    release = state["releases"].get(tag)
    if release is None:
        sys.exit(1)
    jq = args[args.index("--jq") + 1]
    if jq == ".tagName":
        print(tag)
    elif jq == ".isDraft | not":
        print("false" if release["draft"] else "true")
    elif jq == ".assets[].name":
        print("\n".join(release["assets"]))
    else:
        sys.exit(3)
elif args[:2] == ["release", "edit"]:
    tag = args[2]
    if args[3:] == ["--latest"]:
        state["latest"] = tag
    else:
        release = state["releases"][tag]
        release["prerelease"] = False
        release["title"] = args[args.index("--title") + 1]
        release["notes"] = open(args[args.index("--notes-file") + 1]).read()
        if state.get("steal"):
            state["latest"] = tag
else:
    sys.exit(4)
json.dump(state, open(path, "w"))
'''


def promote(tmp_path, tag, releases, latest="v9.9.8", steal=False, tagged=EVERY_PICTURE):
    """Runs the steps of promote-pi.yml after its checkout of a branch that has every picture. The tag on origin
    points at a commit of another line that holds the pictures in tagged, or is missing when tagged is None."""
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load((WORKFLOWS / "promote-pi.yml").read_text())["jobs"]["promote"]["steps"][1:]
    origin, work, tools = tmp_path / "origin.git", tmp_path / "work", tmp_path / "bin"
    tmp_path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--bare", str(origin), cwd=tmp_path)
    found = {"tagged": None, "head": commit(work, EVERY_PICTURE, "branch")}
    if tagged is not None:
        found["tagged"] = commit(tmp_path / "tag", tagged, "tag")
        git("push", "-q", str(origin), "HEAD:refs/tags/%s" % tag, cwd=tmp_path / "tag")
    git("remote", "add", "origin", str(origin), cwd=work)
    (work / "tools").mkdir()
    shutil.copy(ROOT / "tools" / "pi_release_notes.py", work / "tools")
    tools.mkdir()
    (tools / "gh").write_text(FAKE_GH.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
    (tools / "gh").chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"latest": latest, "releases": releases, "calls": [], "steal": steal}))
    env_file = tmp_path / "github_env"
    env_file.write_text("")
    env = {"PATH": "%s:%s" % (tools, os.environ["PATH"]), "HOME": str(tmp_path), "TAG": tag, "FAKE_GH": str(state),
           "GITHUB_REPOSITORY": "owner/repo", "GITHUB_ENV": str(env_file)}
    found.update(failed=None, output="")
    for step in steps:
        result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], cwd=work,
                                env=env, capture_output=True, text=True)
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            env[key] = value
        if result.returncode:
            found.update(failed=step["name"], output=result.stdout + result.stderr)
            break
    found["state"] = json.loads(state.read_text())
    return found


def releases(assets, draft=False):
    return {"v9.9.8": {"draft": False, "prerelease": False, "assets": ["quartermaster.deb"]},
            "pi-v9.9.9": {"draft": draft, "prerelease": True, "assets": sorted(assets)}}


@needs_git
@pytest.mark.parametrize("images", [NEW, OLD])
def test_a_tested_pi_release_becomes_a_full_release_and_the_cardputer_release_stays_latest(tmp_path, images):
    run = promote(tmp_path, "pi-v9.9.9", releases(held(images)))
    assert run["failed"] is None, run["output"]
    state = run["state"]
    release = state["releases"]["pi-v9.9.9"]
    assert release["prerelease"] is False and release["title"] == "Quartermaster 9.9.9 DIY for Raspberry Pi"
    assert all("`%s`" % name in release["notes"] for name in images)
    assert state["latest"] == "v9.9.8"
    edits = [call for call in state["calls"] if call[:2] == ["release", "edit"]]
    assert len(edits) == 1 and "--latest=false" in edits[0] and "--prerelease=false" in edits[0]


@needs_git
def test_a_promoted_release_shows_the_pictures_of_its_own_tag_and_not_those_of_the_branch(tmp_path):
    run = promote(tmp_path / "diy", "pi-v9.9.9", releases(held(NEW)))
    assert run["failed"] is None, run["output"]
    notes = run["state"]["releases"]["pi-v9.9.9"]["notes"]
    for path in SCREENS:
        assert "https://raw.githubusercontent.com/owner/repo/%s/%s" % (run["tagged"], path) in notes, path
    assert run["head"] not in notes
    run = promote(tmp_path / "older", "pi-v9.9.9", releases(held(OLD)), tagged=[pi_release_notes.PICTURE])
    assert run["failed"] is None, run["output"]
    notes = run["state"]["releases"]["pi-v9.9.9"]["notes"]
    assert older_picture(notes, run["tagged"]) and run["head"] not in notes
    assert "the aircraft viewer" not in notes and "`%s`" % OLD[0] in notes


@needs_git
@pytest.mark.parametrize("tag, found, latest, tagged, failed, message", [
    ("v9.9.8", releases(held(NEW)), "v9.9.8", EVERY_PICTURE, "Check the Pi release", "not a Pi release tag"),
    ("pi-v9.9.7", releases(held(NEW)), "v9.9.8", EVERY_PICTURE, "Check the Pi release", "has no release"),
    ("pi-v9.9.9", releases(held(NEW), draft=True), "v9.9.8", EVERY_PICTURE, "Check the Pi release", "is a draft"),
    ("pi-v9.9.9", releases(held(NEW)), "pi-v9.9.9", EVERY_PICTURE, "Check the Pi release",
     "must be a CardputerZero release"),
    ("pi-v9.9.9", releases(held(NEW) - {NEW[1]}), "v9.9.8", EVERY_PICTURE, "Write the release notes", "no image"),
    ("pi-v9.9.9", releases(held(NEW)), "v9.9.8", None, "Write the release notes", "refs/tags/pi-v9.9.9"),
    ("pi-v9.9.9", releases(held(NEW)), "v9.9.8", [], "Write the release notes", "has neither the pictures"),
])
def test_a_pi_release_that_is_not_ready_is_left_as_it_is(tmp_path, tag, found, latest, tagged, failed, message):
    run = promote(tmp_path, tag, found, latest=latest, tagged=tagged)
    assert run["failed"] == failed and message in run["output"], run["output"]
    state = run["state"]
    assert not [call for call in state["calls"] if call[:2] == ["release", "edit"]]
    assert state["releases"]["pi-v9.9.9"]["prerelease"] is True and state["latest"] == latest


@needs_git
def test_the_latest_badge_goes_back_to_the_cardputer_release_if_github_moves_it(tmp_path):
    run = promote(tmp_path, "pi-v9.9.9", releases(held(NEW)), steal=True)
    assert run["failed"] == "Make it a full release" and "It was given back to v9.9.8" in run["output"]
    assert run["state"]["latest"] == "v9.9.8"
    assert run["state"]["calls"][-1] == ["release", "edit", "v9.9.8", "--latest"]
