# Quartermaster Node

A Quartermaster Node is a small ESP32 board plugged into the CardputerZero (or a DIY Quartermaster) by USB. It scans Wi-Fi and, when asked, Bluetooth LE, and tells Quartermaster what it hears. It has no GPS and keeps no logs: Quartermaster stamps every sighting with its own position and writes the log. The firmware is built for four chips:

| Chip | Wi-Fi | Bluetooth LE | Image |
| --- | --- | --- | --- |
| ESP32-C5 | 2.4 and 5 GHz | yes | `quartermaster-node-<version>-esp32c5.bin` |
| ESP32-C3 | 2.4 GHz | yes | `quartermaster-node-<version>-esp32c3.bin` |
| ESP32-C6 | 2.4 GHz | yes | `quartermaster-node-<version>-esp32c6.bin` |
| ESP32-S3 | 2.4 GHz | yes | `quartermaster-node-<version>-esp32s3.bin` |

Quartermaster shares the channels between the nodes it has: the C3, C6 and S3 nodes take the 2.4 GHz channels, so the C5s are free for the many 5 GHz channels. Any mix works, up to 20 nodes in all.

The node and Quartermaster talk over the serial line described in [PROTOCOL.md](PROTOCOL.md).

## Boards

| Board | Flash | PSRAM | USB | Status LED |
| --- | --- | --- | --- | --- |
| Seeed XIAO ESP32-C5 | 8 MB | 8 MB | native | `#LED,gpio,27,1` |
| Espressif ESP32-C5-DevKitC-1 N8R8 | 8 MB | 8 MB | native and USB bridge (either port works) | `#LED,ws2812,27` |
| M5Stack Stamp-C5 | 4 MB | none | native | `#LED,gpio,28` (add `,1` if it lights the wrong way round) |
| LilyGO T-Dongle-C5 and other ESP32-C5 boards | 4 MB or more | optional | either | depends on the board |
| Seeed XIAO ESP32-C3 | 4 MB | none | native | none |
| Espressif ESP32-C3-DevKitM-1 | 4 MB | none | USB bridge | `#LED,ws2812,8` |
| ESP32-C3 Super Mini | 4 MB | none | native | `#LED,gpio,8,1` |
| Seeed XIAO ESP32-C6 | 4 MB | none | native | `#LED,gpio,15,1` |
| Espressif ESP32-C6-DevKitC-1 | 8 MB | none | native and USB bridge | `#LED,ws2812,8` |
| Seeed XIAO ESP32-S3 | 8 MB | 8 MB | native | `#LED,gpio,21,1` |
| Espressif ESP32-S3-DevKitC-1 | 8 MB or more | optional | native and USB bridge | `#LED,ws2812,48` (`38` on v1.1) |

The firmware needs 4 MB of flash, and an ESP32-C5 of revision 1.x, an ESP32-C3 of revision 0.3 or later, or any ESP32-C6 or ESP32-S3 of revision 0.x. PSRAM is used on the C5 when the board has it. On a board without PSRAM, Bluetooth may be left off when memory is short; Wi-Fi always works.

The GPIOs `#LED` takes depend on the chip: C5 0 to 10 and 23 to 28, C3 0 to 10, C6 0 to 11, 14, 15 and 18 to 23, S3 0 to 18, 21, 38 to 42 and 45 to 48. The BOOT button is GPIO 28 on the C5, 9 on the C3 and C6, and 0 on the S3.

## LED

The node does not know which LED a board has, so it uses none until Quartermaster tells it with `#LED` (the table above). The choice is saved on the node. While the node scans, the LED blinks briefly after every sweep. `#IDENT` makes it blink for a few seconds, to find the node among others.

## Wireless nodes

A node does not have to be plugged in. One node stays on the CardputerZero as the **hub**; up to 17 more run on batteries or USB chargers as **air nodes**, scan where they are, and send what they hear to the hub by radio (ESP-NOW). Plugged-in and air nodes together are at most 20. Any chip can be the hub or an air node. The same firmware does all three jobs:

- A node that hears nothing from Quartermaster in its first 10 s away from a computer becomes an air node. While it is plugged into a computer it waits for Quartermaster instead; send `#AIR` to make it an air node there. Plugging it into Quartermaster makes it a USB node again.
- Quartermaster makes the plugged-in node a hub, opens pairing, and adds the new air nodes it is shown.
- A new air node looks for a hub that is pairing, with its LED blinking fast (the LED Quartermaster set while the node was plugged in). Once added it keeps its hub across restarts; while it looks for its hub the LED flashes every 2 s, and while it scans the LED blinks briefly each time it hands its findings to the hub.
- Air nodes start and stop when the hub does, and stop by themselves when they have not heard the hub for 60 s.
- Holding BOOT for 2 s, or sending `#UNPAIR` while it is plugged in, makes a node forget its hub, so it can be added again. Do this for an air node that was removed while it was switched off, or whose hub got a new key. On a Stamp-C5 whose LED is set to GPIO 28 the button is not read, so use `#UNPAIR`.

- An air node that cannot reach the hub goes through another air node that can, up to 3 hops from the hub. It picks the path with the fewest hops and the strongest weakest link, keeps trying the hub itself, and goes back to it as soon as the hub answers. Every node on a path uses the cluster key, so nothing new is shared, and every node still counts towards the 20.

The commands and lines are in the "Wireless nodes" section of [PROTOCOL.md](PROTOCOL.md).

## Other wardriver nodes

The hub also takes nodes running two other wardriver firmwares, on its home channel 6, next to Quartermaster nodes:

- [ESP32 Dual Band Wardriver](https://github.com/justcallmekoko/ESP32DualBandWardriver) by JustCallMeKoko, in node mode without encryption.
- [Piglet](https://github.com/Hamspiced/piglet) mesh nodes by Hamspiced.

They show up when pairing is open, like a Quartermaster node, and are added the same way. The hub speaks their protocol: it answers their requests, hands each one a run of channels from Quartermaster's plan, and turns their records into Quartermaster lines. They count towards the 20 nodes. They scan with their own settings and their own LEDs, and they cannot relay.

The hub's side of their protocol is written from the Dual Band Wardriver's MIT-licensed sources. Piglet's sources (CC BY-NC-SA) were only read for how its nodes behave; none of its code is used. Thanks to both projects.

## Flashing

- **From Quartermaster.** Quartermaster ships the firmware in `quartermaster/assets/node/` and can install and update a node plugged into the CardputerZero.
- **From a browser.** The [flasher page](https://valleytechsolutions.github.io/Quartermaster/flasher/) (below) flashes from Chrome or Edge on a computer, or Chrome on Android.
- **By hand.** With ESP-IDF 5.5.6: `idf.py -B build-esp32c3 -D SDKCONFIG=build-esp32c3/sdkconfig -p PORT flash` in `quartermaster-node/` (or the build folder of another chip), or write the chip's merged image at offset 0 with esptool: `esptool.py --chip esp32c3 write_flash 0x0 quartermaster-node-1.2.0-esp32c3.bin`.

If a board does not enter the download mode by itself, hold BOOT while plugging it in.

The merged image is for a first install: bootloader at 0x2000 on the C5 and at 0 on the others, partition table at 0x8000, app at 0x20000. An update only needs the app, which is the end of the merged image from 0x20000. `quartermaster-node.json` lists, for each chip, the file, the offsets and the checksums of the merged image and of the app inside it.

## Browser flasher

`docs/flasher/` is a page that flashes a node with [ESP Web Tools](https://github.com/esphome/esp-web-tools) 10.5.2, served by GitHub Pages from `/docs` on `main`. Web Serial needs https, which Pages gives. It has two buttons:

- **First install** writes the merged image of the board's chip at 0 and offers to erase the board first. The merged image blanks `nvs` either way, so it always clears the node's name, light and hub.
- **Update, keep settings** writes the app image at `ota_0` and a blank `otadata`, so the board starts the new app, the same as an update from Quartermaster. It cannot check the board's partition table first as Quartermaster does, so it is only for boards that already run Quartermaster Node.

The page, its manifests and copies of the images are made from `quartermaster/assets/node/quartermaster-node.json`. After new images are copied there, run:

```
python3 tools/flasher_page.py            # rewrite docs/flasher/
python3 tools/flasher_page.py --check    # list what is out of date
```

It writes the app of each chip next to its merged image for the update button. It only replaces or removes its own files there (`index.html`, `manifest-*.json`, `otadata-blank.bin`, `quartermaster-node-*.bin`, `notices.txt`).

`tests/test_flasher_page.py` fails when the page no longer matches the bundled firmware.

## Building

```
. $IDF_PATH/export.sh            # ESP-IDF v5.5.6
cd firmware/quartermaster-node
sh build.sh                      # every chip, each in its own build-<chip> folder; or: sh build.sh esp32c3
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
| `main/air.c` | air nodes, relaying |
| `main/enow.c` | the protocol of Dual Band Wardriver and Piglet nodes |
| `main/chip.h` | what differs between the chips |
