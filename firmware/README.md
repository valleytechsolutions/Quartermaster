# Quartermaster Node

A Quartermaster Node is a small ESP32-C5 board plugged into the CardputerZero by USB. It scans Wi-Fi on 2.4 and 5 GHz and, when asked, Bluetooth LE, and tells Quartermaster what it hears. It has no GPS and keeps no logs: Quartermaster stamps every sighting with its own position and writes the log. One firmware runs on every ESP32-C5 board.

The node and Quartermaster talk over the serial line described in [PROTOCOL.md](PROTOCOL.md).

## Boards

| Board | Flash | PSRAM | USB | Status LED |
| --- | --- | --- | --- | --- |
| Seeed XIAO ESP32-C5 | 8 MB | 8 MB | native | `#LED,gpio,27,1` |
| Espressif ESP32-C5-DevKitC-1 N8R8 | 8 MB | 8 MB | native and USB bridge (either port works) | `#LED,ws2812,27` |
| M5Stack Stamp-C5 | 4 MB | none | native | `#LED,gpio,28` (add `,1` if it lights the wrong way round) |
| LilyGO T-Dongle-C5 and other ESP32-C5 boards | 4 MB or more | optional | either | depends on the board |

The firmware needs a chip of revision 1.x and 4 MB of flash. PSRAM is used when the board has it. On a board without PSRAM, Bluetooth may be left off when memory is short; Wi-Fi always works.

## LED

The node does not know which LED a board has, so it uses none until Quartermaster tells it with `#LED` (the table above). The choice is saved on the node. While the node scans, the LED blinks briefly after every sweep. `#IDENT` makes it blink for a few seconds, to find the node among others.

## Wireless nodes

A node does not have to be plugged in. One node stays on the CardputerZero as the **hub**; up to 8 more run on batteries or USB chargers as **air nodes**, scan where they are, and send what they hear to the hub by radio (ESP-NOW). The same firmware does all three jobs:

- A node that hears nothing from Quartermaster in its first 10 s becomes an air node. Plugging it into Quartermaster makes it a USB node again.
- Quartermaster makes the plugged-in node a hub, opens pairing, and adds the new air nodes it is shown.
- A new air node looks for a hub that is pairing, with its LED blinking fast (the LED Quartermaster set while the node was plugged in). Once added it keeps its hub across restarts; while it looks for its hub the LED flashes every 2 s, and while it scans the LED blinks briefly each time it hands its findings to the hub.
- Air nodes start and stop when the hub does, and stop by themselves when they have not heard the hub for 60 s.
- Holding BOOT for 2 s, or sending `#UNPAIR` while it is plugged in, makes a node forget its hub, so it can be added again. Do this for an air node that was removed while it was switched off, or whose hub got a new key. On a Stamp-C5 whose LED is set to GPIO 28 the button is not read, so use `#UNPAIR`.

The commands and lines are in the "Wireless nodes" section of [PROTOCOL.md](PROTOCOL.md).

## Flashing

- **From Quartermaster.** Quartermaster ships the firmware in `quartermaster/assets/node/` and can install and update a node plugged into the CardputerZero.
- **From a browser.** The flasher page in `docs/flasher/` (below) flashes from Chrome or Edge on a computer, or Chrome on Android.
- **By hand.** With ESP-IDF 5.5.6: `idf.py -p PORT flash` in `quartermaster-node/`, or write the merged image at offset 0 with esptool: `esptool.py --chip esp32c5 write_flash 0x0 quartermaster-node-1.1.0-merged.bin`.

If a board does not enter the download mode by itself, hold BOOT while plugging it in.

The merged image is for a first install: bootloader at 0x2000, partition table at 0x8000, app at 0x20000. An update only needs the app image at 0x20000. `quartermaster-node.json` lists the files, offsets and checksums.

## Browser flasher

`docs/flasher/` is a page that flashes a node with [ESP Web Tools](https://github.com/esphome/esp-web-tools) 10.5.2, served by GitHub Pages from `/docs` on `main`. Web Serial needs https, which Pages gives. It has two buttons:

- **First install** writes the merged image at 0 and offers to erase the board first. The merged image blanks `nvs` either way, so it always clears the node's name, light and hub.
- **Update, keep settings** writes the app image at `ota_0` and a blank `otadata`, so the board starts the new app, the same as an update from Quartermaster. It cannot check the board's partition table first as Quartermaster does, so it is only for boards that already run Quartermaster Node.

The page, its manifests and copies of the images are made from `quartermaster/assets/node/quartermaster-node.json`. After new images are copied there, run:

```
python3 tools/flasher_page.py            # rewrite docs/flasher/
python3 tools/flasher_page.py --check    # list what is out of date
```

It only replaces or removes its own files there (`index.html`, `manifest-*.json`, `otadata-blank.bin`, `quartermaster-node-*.bin`).

`tests/test_flasher_page.py` fails when the page no longer matches the bundled firmware.

## Building

```
. $IDF_PATH/export.sh            # ESP-IDF v5.5.6
cd firmware/quartermaster-node
idf.py set-target esp32c5
idf.py build
idf.py merge-bin -o quartermaster-node-merged.bin
python3 package.py               # copies the images and writes quartermaster-node.json
```

The build is reproducible: the same sources give the same images. The host tests of the line format, the reporting rules and the wireless frames run without ESP-IDF:

```
make -C firmware/quartermaster-node/test
```

The Firmware workflow builds the images with Espressif's ESP-IDF v5.5.6 image, runs these tests on every change under `firmware/`, uploads the images, and fails when they differ from the ones in `quartermaster/assets/node/`. Then replace those with the images from the workflow.

## How it works

- Wi-Fi runs as a station that never connects, with power saving off. Each sweep is an active scan of the ordinary channels, then a passive scan of the DFS channels and of 12 to 14 and 169 to 177.
- Bluetooth LE is an active scan with a random address, started only when the plan asks for it.
- The node remembers what it has reported and only reports a device again when it is stronger or weaker than before, after 30 s, or when a name turns up.
- Every line goes to the native USB port and to UART0 at 921600 baud. Scanning never waits for a slow port.
- The node stops scanning when it has heard nothing from Quartermaster for 30 s. An air node instead stops when it has heard nothing from its hub for 60 s.
- An air node scans a few channels at a time and hands its lines to the hub in between, on the hub's channel. The hub answers each handover with its state and any commands waiting for that node; lines the hub did not confirm are sent again.

| File | What it does |
| --- | --- |
| `main/main.c` | start up, commands, timers |
| `main/scan.c` | Wi-Fi sweeps |
| `main/ble.c` | Bluetooth LE scanning |
| `main/uplink.c` | the serial ports and the line queue |
| `main/proto.c` | line formatting, command parsing, checksums |
| `main/seen.c` | what has been reported |
| `main/led.c` | the status LED |
| `main/wire.c` | wireless frames, resending, pairing steps, hub lines |
| `main/radio.c` | ESP-NOW, keys |
| `main/hub.c` | the hub |
| `main/air.c` | air nodes |
