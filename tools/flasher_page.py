#!/usr/bin/env python3
"""Write the browser flasher page from the bundled node firmware: python3 tools/flasher_page.py [--out DIR] [--check]

The page, its two ESP Web Tools manifests and copies of the images all come from
quartermaster/assets/node/quartermaster-node.json, so the page always flashes what Quartermaster ships.
"""

import argparse
import fnmatch
import html
import json
import os
from pathlib import Path
from string import Template
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quartermaster import flasher  # noqa: E402

OUT = ROOT / "docs" / "flasher"
LOGO = ROOT / "assets" / "brand" / "quartermaster-logo.svg"
ESP_WEB_TOOLS = "https://unpkg.com/esp-web-tools@10.5.2/dist/web/install-button.js?module"
OTADATA = "otadata-blank.bin"
FIRST, UPDATE = "manifest-first.json", "manifest-update.json"
NOTICES = "notices.txt"
NOTICES_SOURCE = ROOT / "quartermaster" / "assets" / "node-firmware-notices.txt"
OURS = ("index.html", "manifest-*.json", OTADATA, "quartermaster-node-*.bin", NOTICES)

PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="dark">
<title>Quartermaster Node flasher</title>
<meta name="description" content="Put the Quartermaster Node firmware $version on an ESP32-C5 board from your browser.">
<style>
:root{--bg:#04070a;--surface:#0d1317;--surface-2:#141d23;--line:#202d34;--text:#eaf1ed;--muted:#889992;
--gold:#f5b301;--gold-dim:#604604;--ink:#1a1200;--esp-tools-button-color:#f5b301;--esp-tools-button-text-color:#1a1200;
--mdc-theme-primary:#c48a00}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:1rem/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:42rem;margin:0 auto;padding:24px 16px 40px}
header{display:flex;align-items:center;gap:14px;margin-bottom:8px}
header svg{width:64px;height:64px;flex:none}
h1{margin:0;font-size:1.6rem;line-height:1.2;color:var(--gold);letter-spacing:.02em}
h1 small{display:block;font-size:.95rem;font-weight:400;color:var(--muted);letter-spacing:0}
h2{margin:32px 0 10px;font-size:1.15rem;color:var(--gold)}
p,li{color:var(--text)}
.muted{color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;margin:12px 0}
.version{display:inline-block;margin-top:6px;padding:2px 10px;border:1px solid var(--gold-dim);border-radius:999px;
color:var(--gold);font-weight:600;font-variant-numeric:tabular-nums}
.btn{display:block;width:100%;min-height:52px;padding:12px 16px;border-radius:10px;border:2px solid var(--gold);
font:inherit;font-size:1.1rem;font-weight:700;cursor:pointer;background:var(--gold);color:var(--ink)}
.btn.outline{background:transparent;color:var(--gold)}
.btn:hover{filter:brightness(1.1)}
.btn:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid var(--text);outline-offset:3px}
.card p{margin:10px 0 0}
esp-web-install-button:not(:defined){display:none}
esp-web-install-button:defined+.wait{display:none}
.wait{visibility:hidden;animation:late 0s 6s forwards}
@keyframes late{to{visibility:visible}}
.warn{color:var(--gold)}
ol,ul{padding-left:1.3rem}
li{margin:4px 0}
details{background:var(--surface);border:1px solid var(--line);border-radius:10px;margin:8px 0}
summary{cursor:pointer;padding:12px 14px;font-weight:600}
details>div{padding:0 14px 12px}
a{color:var(--gold)}
footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);font-size:.9rem;color:var(--muted)}
</style>
<script type="module" src="$script"></script>
</head>
<body>
<main>
<header>
$logo
<h1>Quartermaster Node<small>Flash a node from your browser</small></h1>
</header>
<span class="version">Firmware $version</span>

<h2>What is a node?</h2>
<p>A node is a small ESP32-C5 board that you plug into the CardputerZero by USB. It listens for Wi-Fi on 2.4 and
5 GHz, and for Bluetooth when you ask, and tells Quartermaster what it hears. Quartermaster adds where you are and
writes the log. A node can also run on a battery away from the CardputerZero and send what it hears by radio.</p>

<h2>Flash a board</h2>
<ol>
<li>Plug the board into this computer or phone with a USB cable that carries data.</li>
<li>Press a button below and pick the board in the list that opens.</li>
<li>Wait until it says it is done, then plug the board into the CardputerZero.</li>
</ol>

<div class="card">
<esp-web-install-button manifest="$first">
<button class="btn" slot="activate">First install</button>
<p slot="unsupported" class="warn">This browser cannot flash a board. Open this page in Chrome or Edge on a computer,
or in Chrome on Android.</p>
<p slot="not-allowed" class="warn">Flashing only works when this page is opened with https.</p>
</esp-web-install-button>
<p class="wait warn">No button? Check the internet connection and load this page again.</p>
<noscript><p class="warn">Flashing needs JavaScript. Turn it on and load this page again.</p></noscript>
<p>For a new board, or one that runs something else. First install always clears the node's light, country and
hub. Its name stays in Quartermaster. When it asks to erase the board, say yes, so nothing old is left behind.</p>
</div>

<div class="card">
<esp-web-install-button manifest="$update">
<button class="btn outline" slot="activate">Update, keep settings</button>
<p slot="unsupported" class="warn">This browser cannot flash a board.</p>
<p slot="not-allowed" class="warn">Flashing only works when this page is opened with https.</p>
</esp-web-install-button>
<p class="wait warn">No button? Check the internet connection and load this page again.</p>
<p>Only for a board that already runs Quartermaster Node. It keeps the node's light, country and hub. The browser
cannot check how the board was set up, so if the board does not start after an update, use First install.</p>
</div>

<p class="muted">Nothing is sent anywhere: the firmware goes from this page straight to your board.</p>

<h2>Boards</h2>
<ul>
<li>Seeed XIAO ESP32-C5</li>
<li>Espressif ESP32-C5-DevKitC-1</li>
<li>M5Stack Stamp-C5</li>
<li>Other ESP32-C5 boards with 4 MB of flash or more</li>
</ul>

<h2>If the board is not found</h2>
<p>Put the board in download mode, then press the button again.</p>
<details>
<summary>Seeed XIAO ESP32-C5</summary>
<div><p>Hold the BOOT button while you plug the board in. Let go once it is in.</p></div>
</details>
<details>
<summary>M5Stack Stamp-C5</summary>
<div><p>Hold pin G28 to GND while you plug the board in. Let go once it is in.</p></div>
</details>
<details>
<summary>ESP32-C5-DevKitC-1</summary>
<div><p>Hold the BOOT button, press and let go of RESET, then let go of BOOT. The board has two USB-C ports;
either one works.</p></div>
</details>
<p class="muted">Still not found? Try another cable, as some only charge. Close other programs and tabs that use
the board.</p>

<h2>Browsers that work</h2>
<ul>
<li>Chrome or Edge on Windows, Mac, Linux or a Chromebook</li>
<li>Chrome on Android, with a USB-C cable or an OTG adapter</li>
</ul>
<p class="muted">Safari, and every browser on iPhone or iPad, cannot flash a board. If the button says this browser
cannot flash, open the page in Chrome or Edge.</p>

<h2>Flash from Quartermaster instead</h2>
<p>Quartermaster comes with this firmware too. Plug the board into the CardputerZero and open
<strong>Settings &gt; Flash a node</strong>. It can also update the nodes you have added.</p>

<footer>
<p>Quartermaster Node firmware $version for ESP32-C5. Flashing by
<a href="https://github.com/esphome/esp-web-tools">ESP Web Tools</a>.
<a href="https://github.com/valleytechsolutions/Quartermaster">Quartermaster on GitHub</a>.
The firmware includes ESP-IDF and other open source parts: <a href="$notices">their licenses</a>.</p>
</footer>
</main>
</body>
</html>
""")


def logo():
    svg = LOGO.read_text(encoding="utf-8").strip()
    return svg.replace("<svg ", '<svg aria-hidden="true" focusable="false" ', 1)


def manifest(version, parts, erase):
    data = {"name": "Quartermaster Node", "version": version, "new_install_prompt_erase": erase,
            "new_install_improv_wait_time": 0,
            "builds": [{"chipFamily": "ESP32-C5", "parts": [{"path": p, "offset": o} for p, o in parts]}]}
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


def files(source=flasher.MANIFEST):
    """Every file of the page folder, by name, from the bundled firmware index. Damaged firmware stops it."""
    try:
        index, piece = flasher.load(source)
        merged, base = piece("merged")
        app, ota_0 = piece("app")
        version = str(index["version"])
        offsets = {name: int(value) for name, value in index["partition_offsets"].items()}
        otadata = offsets["otadata"]
    except (flasher.FlashError, KeyError, TypeError, ValueError, AttributeError) as e:
        raise SystemExit("The node firmware index is damaged: %s" % e)
    after = [o for o in offsets.values() if o > otadata]
    ota_1 = offsets.get("ota_1")
    if base != 0 or ota_0 != offsets.get("ota_0") or (ota_1 is not None and ota_0 + len(app) > ota_1) or \
            otadata % flasher.SECTOR or (after and min(after) < otadata + flasher.OTADATA_SIZE):
        raise SystemExit("The node firmware index does not match its partitions")
    merged_name, app_name = os.path.basename(index["merged"]["file"]), os.path.basename(index["app"]["file"])
    out = {
        merged_name: merged,
        app_name: app,
        OTADATA: b"\xff" * flasher.OTADATA_SIZE,
        FIRST: manifest(version, [(merged_name, base)], True),
        UPDATE: manifest(version, [(app_name, ota_0), (OTADATA, otadata)], False),
        NOTICES: NOTICES_SOURCE.read_bytes(),
    }
    out["index.html"] = PAGE.substitute(version=html.escape(version), script=ESP_WEB_TOOLS, logo=logo(),
                                        first=FIRST, update=UPDATE, notices=NOTICES).encode("utf-8")
    return out


def ours(name):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in OURS)


def stale(out, wanted):
    if not out.is_dir():
        return []
    return sorted(p.name for p in out.iterdir() if p.is_file() and p.name not in wanted and ours(p.name))


def write(out=OUT, source=flasher.MANIFEST):
    wanted = files(source)
    out.mkdir(parents=True, exist_ok=True)
    for name in stale(out, wanted):
        (out / name).unlink()
    for name, data in wanted.items():
        path = out / name
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
    return sorted(wanted)


def differences(out=OUT, source=flasher.MANIFEST):
    wanted = files(source)
    changed = [n for n, d in wanted.items() if not (out / n).is_file() or (out / n).read_bytes() != d]
    return sorted(changed + stale(out, wanted))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true", help="only report files that are out of date")
    args = ap.parse_args()
    if args.check:
        changed = differences(args.out)
        for name in changed:
            print("out of date:", name)
        return 1 if changed else 0
    for name in write(args.out):
        print(args.out / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
