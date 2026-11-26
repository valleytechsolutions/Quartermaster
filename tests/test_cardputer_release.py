import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
BANNER = ('**This is the CardputerZero version of Quartermaster.** Building one on a Raspberry Pi with a Waveshare LCD'
          ' HAT? Use the DIY version: the names of its releases end in "for Raspberry Pi", and the'
          ' [DIY guide](https://github.com/owner/repo/blob/main/docs/diy.md) shows how to flash it.')
OLD = "- New: Deep scan, in Settings.\n- Bluetooth works the same as before."
TITLED = "Give a release published by hand its title and banner"
CHANGES = "- A change for this version.\n- Another change.\n"
PACKAGES = ["quartermaster.deb", "quartermaster_9.9.9_all.deb", "SHA256SUMS"]

needs_tools = pytest.mark.skipif(not all(shutil.which(tool) for tool in ("bash", "git", "awk", "sed")),
                                 reason="needs bash, git, awk and sed")


def workflow(name):
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((WORKFLOWS / name).read_text())


def step(name, job, title):
    return next(item["run"] for item in workflow(name)["jobs"][job]["steps"] if item.get("name") == title)


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
    elif jq == ".body":
        print(release["notes"])
    elif jq == ".name":
        print(release["title"])
    elif jq == ".assets[].name":
        print("\n".join(release["assets"]))
    else:
        sys.exit(3)
elif args[:2] == ["release", "create"]:
    tag = args[2]
    notes = open(args[args.index("--notes-file") + 1], newline="").read()
    state["releases"][tag] = {"draft": False, "title": args[args.index("--title") + 1], "notes": notes, "assets": []}
    if "--latest=false" not in args and "--prerelease" not in args:
        state["latest"] = tag
elif args[:2] == ["release", "upload"]:
    state["releases"][args[2]]["assets"] += [os.path.basename(name) for name in args[3:]]
elif args[:2] == ["release", "edit"]:
    tag = args[2]
    if args[3:] == ["--latest"]:
        state["latest"] = tag
    else:
        release = state["releases"][tag]
        release["title"] = args[args.index("--title") + 1]
        release["notes"] = open(args[args.index("--notes-file") + 1], newline="").read()
        if state.get("steal"):
            state["latest"] = tag
else:
    sys.exit(4)
json.dump(state, open(path, "w"))
'''


def fake_gh(tmp_path, releases, latest, steal=False):
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "gh").write_text(FAKE_GH.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
    (tools / "gh").chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"latest": latest, "releases": releases, "calls": [], "steal": steal}))
    return {"PATH": "%s:%s" % (tools, os.environ["PATH"]), "HOME": str(tmp_path), "FAKE_GH": str(state),
            "GITHUB_REPOSITORY": "owner/repo"}, state


def bash(script, cwd, env):
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd=cwd, env=env,
                          capture_output=True, text=True)


def changelog_section(version):
    lines = (ROOT / "CHANGELOG.md").read_text().splitlines(keepends=True)
    start = lines.index("## %s\n" % version) + 1
    end = next((index for index in range(start, len(lines)) if lines[index].startswith("## ")), len(lines))
    return "".join(lines[start:end]).lstrip("\n")


@needs_tools
def test_a_new_cardputer_release_starts_with_the_banner_and_then_the_changelog_of_its_version(tmp_path):
    version = json.loads((ROOT / "app-builder.json").read_text())["version"]
    shutil.copy(ROOT / "CHANGELOG.md", tmp_path)
    (tmp_path / "build").mkdir()
    env = {"PATH": os.environ["PATH"], "VERSION": version, "GITHUB_REPOSITORY": "owner/repo"}
    result = bash(step("release.yml", "release", "Release notes for this version"), tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    notes = (tmp_path / "build" / "notes.md").read_text()
    assert notes == BANNER + "\n\n" + changelog_section(version)
    assert notes.splitlines()[2].startswith("- ") and "—" not in notes.splitlines()[0]


@needs_tools
def test_a_version_without_a_changelog_section_gets_no_release(tmp_path):
    shutil.copy(ROOT / "CHANGELOG.md", tmp_path)
    (tmp_path / "build").mkdir()
    env = {"PATH": os.environ["PATH"], "VERSION": "0.0.1", "GITHUB_REPOSITORY": "owner/repo"}
    result = bash(step("release.yml", "release", "Release notes for this version"), tmp_path, env)
    assert result.returncode != 0 and not (tmp_path / "build" / "notes.md").exists()


@needs_tools
def test_a_new_cardputer_release_is_called_for_cardputerzero_and_still_takes_the_latest_badge(tmp_path):
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    "commit", "-q", "--allow-empty", "-m", "x"], cwd=work, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True,
                          text=True).stdout.strip()
    (work / "build").mkdir()
    (work / "build" / "notes.md").write_text(BANNER + "\n\n" + OLD + "\n")
    env, state = fake_gh(tmp_path, {"v9.9.8": {"draft": False, "title": "x", "notes": "", "assets": []}}, "v9.9.8")
    env.update(TAG="v9.9.9", VERSION="9.9.9")
    result = bash(step("release.yml", "release", "Publish the Cardputer package"), work, env)
    assert result.returncode == 0, result.stdout + result.stderr
    found = json.loads(state.read_text())
    made = [call for call in found["calls"] if call[:2] == ["release", "create"]]
    assert made == [["release", "create", "v9.9.9", "--target", head,
                     "--title", "Quartermaster 9.9.9 for CardputerZero", "--notes-file", "build/notes.md",
                     "build/cardputer/quartermaster.deb", "build/cardputer/quartermaster_9.9.9_all.deb",
                     "build/cardputer/SHA256SUMS"]]
    assert found["releases"]["v9.9.9"]["notes"].startswith(BANNER + "\n\n- New: Deep scan")
    assert found["latest"] == "v9.9.9"
    publish = step("release.yml", "release", "Publish the Cardputer package")
    assert "--latest" not in publish and "--prerelease" not in publish and "gh release edit" not in publish
    titled = step("release.yml", "release", TITLED)
    assert "--prerelease" not in titled and titled.count("gh release edit") == 2
    assert 'gh release edit "$TAG" --title "$title" --notes-file build/titled.md\n' in titled
    assert 'gh release edit "$latest" --latest\n' in titled


def release_run(tmp_path, releases, latest="v9.9.9", steal=False):
    """Runs release.yml from its release notes on, for v9.9.9 at the commit the tag points to."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    for args in (["init", "-q", "--bare", str(origin)], ["init", "-q", str(work)]):
        subprocess.run(["git", *args], check=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], cwd=work, check=True)
    subprocess.run(git + ["push", "-q", str(origin), "HEAD:refs/tags/v9.9.9"], cwd=work, check=True,
                   capture_output=True)
    subprocess.run(git + ["remote", "add", "origin", str(origin)], cwd=work, check=True)
    (work / "CHANGELOG.md").write_text("# Changelog\n\n## 9.9.9\n\n" + CHANGES + "\n## 9.9.8\n\n- Older.\n")
    (work / "build").mkdir()
    env, state = fake_gh(tmp_path, releases, latest, steal)
    env.update(TAG="v9.9.9", VERSION="9.9.9")
    output = ""
    for title in ("Release notes for this version", "Publish the Cardputer package", TITLED):
        result = bash(step("release.yml", "release", title), work, env)
        output += result.stdout + result.stderr
        if result.returncode:
            return title, output, json.loads(state.read_text())
    return None, output, json.loads(state.read_text())


def by_hand(notes, title="v9.9.9"):
    return {"draft": False, "title": title, "notes": notes, "assets": []}


@needs_tools
@pytest.mark.parametrize("typed", ["- Typed on GitHub.\n- And more.", "Typed on GitHub.\r\n\r\n- One line.",
                                   "\n\n- Typed after blank lines."])
def test_a_cardputer_release_published_by_hand_gets_the_title_and_the_banner_above_its_own_notes(tmp_path, typed):
    failed, output, state = release_run(tmp_path, {"v9.9.9": by_hand(typed)})
    assert failed is None, output
    release = state["releases"]["v9.9.9"]
    assert release["title"] == "Quartermaster 9.9.9 for CardputerZero" and release["assets"] == PACKAGES
    assert release["notes"] == BANNER + "\n\n" + typed.lstrip("\n") + "\n"
    assert release["notes"].count("This is the CardputerZero version") == 1
    assert state["latest"] == "v9.9.9" and "v9.9.9 now has its title and banner." in output
    assert not [call for call in state["calls"] if call[:2] == ["release", "create"]]


@needs_tools
def test_a_cardputer_release_published_by_hand_without_notes_gets_the_changes_of_its_version(tmp_path):
    failed, output, state = release_run(tmp_path, {"v9.9.9": by_hand("")})
    assert failed is None, output
    assert state["releases"]["v9.9.9"]["notes"] == BANNER + "\n\n" + CHANGES


@needs_tools
@pytest.mark.parametrize("releases", [{}, {"v9.9.9": {"draft": False, "title": "Quartermaster 9.9.9 for CardputerZero",
                                                      "notes": BANNER + "\n\n- Typed on GitHub.\n",
                                                      "assets": PACKAGES}}])
def test_a_release_that_already_has_its_title_and_banner_is_not_edited(tmp_path, releases):
    failed, output, state = release_run(tmp_path, releases, latest="v9.9.8" if not releases else "v9.9.9")
    assert failed is None, output
    assert not edits(state) and "v9.9.9 has its title and banner." in output
    assert state["releases"]["v9.9.9"]["title"] == "Quartermaster 9.9.9 for CardputerZero"
    assert state["releases"]["v9.9.9"]["notes"].startswith(BANNER + "\n\n- ")
    assert state["latest"] == "v9.9.9"


@needs_tools
def test_a_release_published_by_hand_keeps_the_latest_badge_where_it_was(tmp_path):
    releases = {"v9.9.8": cardputer(), "v9.9.9": by_hand("- Typed on GitHub.")}
    failed, output, state = release_run(tmp_path, releases, latest="v9.9.8", steal=True)
    assert failed == TITLED and "v9.9.9 took the Latest badge from v9.9.8. It was given back to v9.9.8." in output
    assert state["latest"] == "v9.9.8" and state["calls"][-1] == ["release", "edit", "v9.9.8", "--latest"]
    assert state["releases"]["v9.9.9"]["title"] == "Quartermaster 9.9.9 for CardputerZero"
    failed, output, state = release_run(tmp_path / "kept", releases, latest="v9.9.8")
    assert failed is None and state["latest"] == "v9.9.8", output


def banner_run(tmp_path, tag, releases, latest="v9.9.8", steal=False):
    work = tmp_path / "work"
    work.mkdir(parents=True)
    env, state = fake_gh(tmp_path, releases, latest, steal)
    env.update(TAG=tag, GITHUB_ENV=str(tmp_path / "github_env"))
    (tmp_path / "github_env").write_text("")
    output = ""
    for item in workflow("cardputer-notes.yml")["jobs"]["notes"]["steps"]:
        result = bash(item["run"], work, env)
        output += result.stdout + result.stderr
        for line in (tmp_path / "github_env").read_text().splitlines():
            key, _, value = line.partition("=")
            env[key] = value
        if result.returncode:
            return item["name"], output, json.loads(state.read_text())
    return None, output, json.loads(state.read_text())


def cardputer(notes=OLD, title="Quartermaster 9.9.8", draft=False):
    return {"draft": draft, "title": title, "notes": notes, "assets": ["quartermaster.deb", "SHA256SUMS"]}


def edits(state):
    return [call for call in state["calls"] if call[:2] == ["release", "edit"]]


def test_the_banner_workflow_is_run_by_hand_for_one_tag_and_never_moves_the_badge_itself():
    found = workflow("cardputer-notes.yml")
    assert set(found[True]) == {"workflow_dispatch"}
    tag = found[True]["workflow_dispatch"]["inputs"]["tag"]
    assert tag["required"] is True and "v1.2.5" in tag["description"]
    assert found["permissions"] == {"contents": "write"}
    assert found["concurrency"] == {"group": "release-${{ inputs.tag }}"}
    job = found["jobs"]["notes"]
    assert job["env"] == {"TAG": "${{ inputs.tag }}", "GH_TOKEN": "${{ github.token }}",
                          "GH_REPO": "${{ github.repository }}"}
    text = "\n".join(item["run"] for item in job["steps"])
    assert "--prerelease" not in text and text.count("--latest") == 1
    assert 'gh release edit "$TAG" --title "$title" --notes-file build/notes.md\n' in text
    assert 'gh release edit "$LATEST" --latest\n' in text


@needs_tools
@pytest.mark.parametrize("tag, latest", [("v9.9.8", "v9.9.8"), ("v9.9.7", "v9.9.8")])
def test_a_cardputer_release_gets_its_title_and_the_banner_above_the_notes_it_had(tmp_path, tag, latest):
    releases = {"v9.9.8": cardputer(), "v9.9.7": cardputer(title="v9.9.7")}
    failed, output, state = banner_run(tmp_path, tag, releases, latest)
    assert failed is None, output
    release = state["releases"][tag]
    assert release["title"] == "Quartermaster %s for CardputerZero" % tag[1:]
    assert release["notes"] == BANNER + "\n\n" + OLD + "\n"
    assert edits(state) == [["release", "edit", tag, "--title", release["title"], "--notes-file", "build/notes.md"]]
    assert state["latest"] == latest and "is still the Latest release" in output


@needs_tools
def test_running_it_again_changes_nothing(tmp_path):
    done = cardputer(notes=BANNER + "\n\n" + OLD + "\n", title="Quartermaster 9.9.8 for CardputerZero")
    failed, output, state = banner_run(tmp_path, "v9.9.8", {"v9.9.8": done})
    assert failed is None and "v9.9.8 already has its title and banner." in output
    assert not edits(state) and state["releases"]["v9.9.8"] == done


@needs_tools
@pytest.mark.parametrize("notes, kept", [
    ("", ""),
    ("\n\n" + OLD, OLD),
    (BANNER + "\n\n" + OLD, OLD),
    (BANNER.replace("owner/repo", "someone/else") + "\r\n\r\n" + OLD.replace("\n", "\r\n"), OLD.replace("\n", "\r\n")),
    (OLD + "\n\n" + BANNER, OLD),
    ("**This is the CardputerZero version of Quartermaster.** An older banner.\n" + OLD, OLD),
])
def test_the_banner_is_never_there_twice_and_the_notes_are_kept(tmp_path, notes, kept):
    failed, output, state = banner_run(tmp_path, "v9.9.8", {"v9.9.8": cardputer(notes=notes)})
    assert failed is None, output
    wanted = BANNER + "\n" + ("\n" + kept + "\n" if kept else "")
    assert state["releases"]["v9.9.8"]["notes"] == wanted
    assert state["releases"]["v9.9.8"]["notes"].count("This is the CardputerZero version") == 1
    failed, output, again = banner_run(tmp_path / "again", "v9.9.8", state["releases"])
    assert failed is None and "already has its title and banner" in output and not edits(again)


@needs_tools
def test_a_release_with_the_banner_and_an_old_title_gets_the_new_title_and_keeps_one_banner(tmp_path):
    failed, output, state = banner_run(tmp_path, "v9.9.8", {"v9.9.8": cardputer(notes=BANNER + "\n\n" + OLD)})
    assert failed is None, output
    assert state["releases"]["v9.9.8"] == cardputer(notes=BANNER + "\n\n" + OLD + "\n",
                                                    title="Quartermaster 9.9.8 for CardputerZero")


@needs_tools
@pytest.mark.parametrize("tag, releases, latest, message", [
    ("pi-v9.9.9", {"v9.9.8": cardputer(), "pi-v9.9.9": cardputer(title="Quartermaster 9.9.9 DIY for Raspberry Pi")},
     "v9.9.8", "pi-v9.9.9 is not a CardputerZero release tag"),
    ("9.9.8", {"v9.9.8": cardputer()}, "v9.9.8", "9.9.8 is not a CardputerZero release tag"),
    ("v9.9.8-rc1", {"v9.9.8": cardputer()}, "v9.9.8", "is not a CardputerZero release tag"),
    ("v9.9.6", {"v9.9.8": cardputer()}, "v9.9.8", "v9.9.6 is a draft or has no release"),
    ("v9.9.9", {"v9.9.8": cardputer(), "v9.9.9": cardputer(draft=True)}, "v9.9.8",
     "v9.9.9 is a draft or has no release"),
    ("v9.9.8", {"v9.9.8": cardputer()}, None, "There is no Latest release to keep"),
])
def test_a_tag_that_is_not_a_published_cardputer_release_is_left_alone(tmp_path, tag, releases, latest, message):
    before = json.loads(json.dumps(releases))
    failed, output, state = banner_run(tmp_path, tag, releases, latest)
    assert failed == "Check the CardputerZero release" and message in output
    assert not edits(state) and state["releases"] == before and state["latest"] == latest


@needs_tools
def test_the_latest_badge_goes_back_if_github_moves_it(tmp_path):
    releases = {"v9.9.8": cardputer(), "v9.9.7": cardputer(title="v9.9.7")}
    failed, output, state = banner_run(tmp_path, "v9.9.7", releases, steal=True)
    assert failed == "Add the title and the banner" and "v9.9.7 took the Latest badge from v9.9.8" in output
    assert "It was given back to v9.9.8." in output and state["latest"] == "v9.9.8"
    assert state["calls"][-1] == ["release", "edit", "v9.9.8", "--latest"]


@needs_tools
def test_new_and_older_cardputer_releases_get_the_same_banner(tmp_path):
    version = json.loads((ROOT / "app-builder.json").read_text())["version"]
    shutil.copy(ROOT / "CHANGELOG.md", tmp_path)
    (tmp_path / "build").mkdir()
    env = {"PATH": os.environ["PATH"], "VERSION": version, "GITHUB_REPOSITORY": "owner/repo"}
    assert bash(step("release.yml", "release", "Release notes for this version"), tmp_path, env).returncode == 0
    new = (tmp_path / "build" / "notes.md").read_text().splitlines()[0]
    failed, output, state = banner_run(tmp_path / "older", "v9.9.8", {"v9.9.8": cardputer()})
    assert failed is None, output
    assert new == state["releases"]["v9.9.8"]["notes"].splitlines()[0] == BANNER
