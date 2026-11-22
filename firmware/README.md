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

## Flashing

- **From Quartermaster.** Quartermaster ships the firmware in `quartermaster/assets/node/` and can install and update a node plugged into the CardputerZero.
- **From a browser.** A web flasher page will follow.
- **By hand.** With ESP-IDF 5.5.6: `idf.py -p PORT flash` in `quartermaster-node/`, or write the merged image at offset 0 with esptool: `esptool.py --chip esp32c5 write_flash 0x0 quartermaster-node-1.0.0-merged.bin`.

If a board does not enter the download mode by itself, hold BOOT while plugging it in.

The merged image is for a first install: bootloader at 0x2000, partition table at 0x8000, app at 0x20000. An update only needs the app image at 0x20000. `quartermaster-node.json` lists the files, offsets and checksums.

## Building

```
. $IDF_PATH/export.sh            # ESP-IDF v5.5.6
cd firmware/quartermaster-node
idf.py set-target esp32c5
idf.py build
idf.py merge-bin -o quartermaster-node-merged.bin
python3 package.py               # copies the images and writes quartermaster-node.json
```

The build is reproducible: the same sources give the same images. The host tests of the line format and the reporting rules run without ESP-IDF:

```
make -C firmware/quartermaster-node/test
```

The Firmware workflow builds the images with Espressif's ESP-IDF v5.5.6 image, runs these tests on every change under `firmware/`, uploads the images, and fails when they differ from the ones in `quartermaster/assets/node/`. Then replace those with the images from the workflow.

## How it works

- Wi-Fi runs as a station that never connects, with power saving off. Each sweep is an active scan of the ordinary channels, then a passive scan of the DFS channels and of 12 to 14 and 169 to 177.
- Bluetooth LE is an active scan with a random address, started only when the plan asks for it.
- The node remembers what it has reported and only reports a device again when it is stronger or weaker than before, after 30 s, or when a name turns up.
- Every line goes to the native USB port and to UART0 at 921600 baud. Scanning never waits for a slow port.
- The node stops scanning when it has heard nothing from Quartermaster for 30 s.

| File | What it does |
| --- | --- |
| `main/main.c` | start up, commands, timers |
| `main/scan.c` | Wi-Fi sweeps |
| `main/ble.c` | Bluetooth LE scanning |
| `main/uplink.c` | the serial ports and the line queue |
| `main/proto.c` | line formatting, command parsing, checksums |
| `main/seen.c` | what has been reported |
| `main/led.c` | the status LED |
