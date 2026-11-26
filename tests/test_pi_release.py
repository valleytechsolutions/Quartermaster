import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools import pi_release_notes

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
REF = "0123456789abcdef0123456789abcdef01234567"
NEW = ["quartermaster-pi-9.9.9-waveshare-1.44inch-lcd-hat.img.xz", "quartermaster-pi-9.9.9-waveshare-1.3inch-lcd-hat.img.xz"]
OLD = ["quartermaster-pi-9.9.9-st7735-128.img.xz", "quartermaster-pi-9.9.9-st7789-240.img.xz"]
REST = ["quartermaster-pi_9.9.9_all.deb", "SHA256SUMS", "quartermaster-imager.json", "quartermaster.rpi-imager-manifest"]


def held(images):
    return {*images, *(name + ".sha256" for name in images), *REST}


def test_the_notes_say_which_version_they_are_for_and_show_each_hat_with_its_image():
    text = pi_release_notes.notes("owner/repo", "pi-v9.9.9", REF)
    first = text.splitlines()[0]
    assert first.startswith("**This is the DIY version of Quartermaster**, for a Raspberry Pi with a Waveshare")
    assert "M5Stack CardputerZero version" in first and "(https://github.com/owner/repo/releases/latest)" in first
    for (hat, label, pixels, chip), name in zip(pi_release_notes.HATS, NEW):
        row = next(line for line in text.splitlines() if "**Waveshare %s**" % label in line)
        assert "<img src='https://www.waveshare.com/media/catalog/product/1/_/%s-1.jpg'" % pi_release_notes.page(label) in row
        assert "<sub>%s screen, %s</sub>" % (pixels, chip) in row and row.endswith("| `%s` |" % name)
    assert text.index("| Your HAT | Its image |") < text.index("Photos by Waveshare.") < text.index("### What it looks like")
    assert "https://raw.githubusercontent.com/owner/repo/%s/docs/screens/waveshare-preview.png" % REF in text
    for words in ("Download `quartermaster.rpi-imager-manifest` below and open it", "then **Quartermaster 9.9.9**",
                  "can only join a 2.4 GHz one", "On **Localisation**, choose your capital city", "**Use custom**",
                  "user `pi` with password `pi`", "`ssh pi@quartermaster.local`", "Change the password with `passwd`",
                  "press **K1** to select. **K2** goes back, and **K3**", "`~/quartermaster/logs`",
                  "(https://github.com/owner/repo/blob/pi-v9.9.9/docs/diy.md)",
                  "The images are added to this release shortly after it is published.",
                  "While this is the newest Pi release, its images are rebuilt every month"):
        assert words in text, words
    assert "—" not in text and not any(name in text for name in OLD)


def test_the_notes_of_a_release_name_the_images_it_holds():
    for images in (NEW, OLD):
        text = pi_release_notes.notes("o/r", "pi-v9.9.9", REF, held(images))
        assert all("`%s`" % name in text for name in images)
        assert "shortly after it is published" not in text
    text = pi_release_notes.notes("o/r", "pi-v9.9.9", REF, held(NEW) | held(OLD))
    assert all("`%s`" % name in text for name in NEW) and not any(name in text for name in OLD)


@pytest.mark.parametrize("assets, message", [
    (held(NEW) - {NEW[0]}, "no image with its .sha256 for the Waveshare 1.44inch LCD HAT"),
    (held(OLD) - {OLD[1] + ".sha256"}, "no image with its .sha256 for the Waveshare 1.3inch LCD HAT"),
    (held(NEW) - {"quartermaster.rpi-imager-manifest"}, "no quartermaster.rpi-imager-manifest"),
    (held(NEW) - {"quartermaster-imager.json"}, "no quartermaster-imager.json"),
    (set(REST), "no image"),
])
def test_the_notes_refuse_a_release_without_its_images_or_imager_files(assets, message):
    with pytest.raises(ValueError, match=message):
        pi_release_notes.notes("o/r", "pi-v9.9.9", REF, assets)


def test_the_notes_need_a_pi_tag_and_a_commit(tmp_path, capsys):
    for tag in ("v9.9.9", "pi-v9.9", "pi-v9.9.9-rc1"):
        with pytest.raises(ValueError, match="not a Pi release tag"):
            pi_release_notes.notes("o/r", tag, REF)
    for ref in ("main", REF[:12], REF.upper()):
        with pytest.raises(ValueError, match="not a commit"):
            pi_release_notes.notes("o/r", "pi-v9.9.9", ref)
    assets = tmp_path / "assets.txt"
    assets.write_text("\n".join(sorted(held(OLD))) + "\n\n")
    assert pi_release_notes.main(["--repo", "o/r", "--tag", "pi-v9.9.9", "--ref", REF, "--assets", str(assets)]) == 0
    assert capsys.readouterr().out == pi_release_notes.notes("o/r", "pi-v9.9.9", REF, held(OLD))
    for wrong in (["--tag", "v9.9.9", "--ref", REF], ["--tag", "pi-v9.9.9", "--ref", "main"], ["--ref", REF]):
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


def promote(tmp_path, tag, releases, latest="v9.9.8", steal=False):
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load((WORKFLOWS / "promote-pi.yml").read_text())["jobs"]["promote"]["steps"][1:]
    work, tools = tmp_path / "work", tmp_path / "bin"
    work.mkdir()
    tools.mkdir()
    (work / "tools").symlink_to(ROOT / "tools")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    "commit", "-q", "--allow-empty", "-m", "x"], cwd=work, check=True)
    (tools / "gh").write_text(FAKE_GH.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
    (tools / "gh").chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"latest": latest, "releases": releases, "calls": [], "steal": steal}))
    env_file = tmp_path / "github_env"
    env_file.write_text("")
    env = {"PATH": "%s:%s" % (tools, os.environ["PATH"]), "HOME": str(tmp_path), "TAG": tag, "FAKE_GH": str(state),
           "GITHUB_REPOSITORY": "owner/repo", "GITHUB_ENV": str(env_file)}
    for step in steps:
        result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], cwd=work,
                                env=env, capture_output=True, text=True)
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            env[key] = value
        if result.returncode:
            return step["name"], result.stdout + result.stderr, json.loads(state.read_text())
    return None, "", json.loads(state.read_text())


def releases(assets, draft=False):
    return {"v9.9.8": {"draft": False, "prerelease": False, "assets": ["quartermaster.deb"]},
            "pi-v9.9.9": {"draft": draft, "prerelease": True, "assets": sorted(assets)}}


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
@pytest.mark.parametrize("images", [NEW, OLD])
def test_a_tested_pi_release_becomes_a_full_release_and_the_cardputer_release_stays_latest(tmp_path, images):
    failed, output, state = promote(tmp_path, "pi-v9.9.9", releases(held(images)))
    assert failed is None, output
    release = state["releases"]["pi-v9.9.9"]
    assert release["prerelease"] is False and release["title"] == "Quartermaster 9.9.9 DIY for Raspberry Pi"
    assert all("`%s`" % name in release["notes"] for name in images)
    assert state["latest"] == "v9.9.8"
    edits = [call for call in state["calls"] if call[:2] == ["release", "edit"]]
    assert len(edits) == 1 and "--latest=false" in edits[0] and "--prerelease=false" in edits[0]


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
@pytest.mark.parametrize("tag, found, latest, failed, message", [
    ("v9.9.8", releases(held(NEW)), "v9.9.8", "Check the Pi release", "not a Pi release tag"),
    ("pi-v9.9.7", releases(held(NEW)), "v9.9.8", "Check the Pi release", "has no release"),
    ("pi-v9.9.9", releases(held(NEW), draft=True), "v9.9.8", "Check the Pi release", "is a draft"),
    ("pi-v9.9.9", releases(held(NEW)), "pi-v9.9.9", "Check the Pi release", "must be a CardputerZero release"),
    ("pi-v9.9.9", releases(held(NEW) - {NEW[1]}), "v9.9.8", "Write the release notes", "no image"),
])
def test_a_pi_release_that_is_not_ready_is_left_as_it_is(tmp_path, tag, found, latest, failed, message):
    where, output, state = promote(tmp_path, tag, found, latest=latest)
    assert where == failed and message in output
    assert not [call for call in state["calls"] if call[:2] == ["release", "edit"]]
    assert state["releases"]["pi-v9.9.9"]["prerelease"] is True and state["latest"] == latest


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_the_latest_badge_goes_back_to_the_cardputer_release_if_github_moves_it(tmp_path):
    where, output, state = promote(tmp_path, "pi-v9.9.9", releases(held(NEW)), steal=True)
    assert where == "Make it a full release" and "It was given back to v9.9.8" in output
    assert state["latest"] == "v9.9.8"
    assert state["calls"][-1] == ["release", "edit", "v9.9.8", "--latest"]
