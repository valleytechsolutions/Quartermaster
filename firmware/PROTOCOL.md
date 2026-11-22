# Quartermaster Node serial protocol, version 1

A Quartermaster Node is an ESP32-C5 that scans Wi-Fi (2.4 and 5 GHz) and, when asked, Bluetooth LE, and reports what it hears to Quartermaster over USB. It keeps no logs and has no GPS: Quartermaster stamps every sighting with its own fix.

## Link

- The node writes every line to the native USB Serial/JTAG port and to UART0 (TX GPIO11, RX GPIO12) at 921600 baud, 8N1, so either USB port of a board works. It reads commands from both.
- Lines are ASCII and end with `\n`. A line is at most 240 bytes, `\n` included.
- Node lines start with `@`, a type letter and a comma, and end with `*` and two upper-case hex digits: the XOR of every byte after `@` and before `*`, as in NMEA. Anything else the host reads (ROM boot text, logs) is ignored.
- Host lines start with `#` and end with `\n` (a `\r` before it is ignored). The checksum is optional for the host (`#STOP` and `#STOP*hh` are both accepted); a wrong checksum is refused with `@A,...,<command>,err,checksum` and the command is not carried out.
- Fields are separated by commas. Text that could hold a comma or a non-ASCII byte (SSID, device name) is sent as lower-case hex of its raw bytes. An empty field is empty. In the free text of `@A` details and `@L` notes, a comma, a `*` or a byte outside printable ASCII is sent as `_`.
- `seq` counts every `@W`, `@B`, `@S`, `@A` and `@L` line from 0 at boot, wrapping at 2^32. The host uses gaps to count lost lines.
- Opening the port must not reset the board: the host builds the port unopened with DTR high and RTS low, opens it, then sets DTR low, so RTS is never high alone (that resets the board on both kinds of USB port). It never changes the lines afterwards, except to reset the board on purpose.
- The node never waits long for a port. On the port the last host line came from it retries a line for up to 0.5 s; on the other port it skips a line that does not fit. A line it could not queue at all is counted in `lines dropped`. Lines written before the host opened the port can arrive first, so the host ignores sightings until the `@A` of its first command.

## Node to host

| Line | Fields | When |
| --- | --- | --- |
| `@H` | `1` (protocol), node id (12 hex digits, the factory MAC), firmware version, chip revision (`1.0`), flash MB, PSRAM MB, board, capabilities, country, plan version, state | at boot, every 10 s while idle, every 30 s while running, and in reply to `#HELLO` |
| `@W` | seq, age ms, BSSID, RSSI, channel, frequency MHz, auth mode, pairwise cipher, group cipher, flags (hex), SSID (hex) | a network to report |
| `@B` | seq, age ms, address, address type (`0` public, `1` random), RSSI, flags (hex), manufacturer id (hex, 4 digits, or empty), name (hex) | a Bluetooth LE device to report |
| `@S` | seq, uptime ms, sweeps, last sweep ms, networks reported, devices reported, lines dropped, plan version, free heap bytes, state | every 2 s while running |
| `@A` | seq, command, `ok` or `err`, detail | in reply to every host command |
| `@L` | seq, text | a note worth showing, such as a failed scan |

- `node id` is lower-case hex. `chip revision` is major.minor. `board` is the node's own guess: `stamp-c5` (4 MB flash inside the chip package, no PSRAM), `c5-8mb` (8 MB flash) or `c5` (anything else, such as a 4 MB board with external flash). `capabilities` is a `+` list: `wifi24`, `wifi5`, `ble`; `ble` is left out after the node has refused Bluetooth for lack of memory. `state` is `idle` or `run`. `plan version` is the version of the plan in use, `0` before the first. `country` is the country in use.
- `age ms` is how long before the line was written the sighting happened. For Wi-Fi it is counted from the middle of the scan that heard the network, so it is off by at most half a scan (one scan covers either the active or the passive channels of a sweep).
- `networks reported` and `devices reported` count the entries added to the table since it was last cleared; a device that was dropped from a full table and comes back counts again.
- `auth mode`, `pairwise cipher` and `group cipher` are the numbers of ESP-IDF 5.5 `wifi_auth_mode_t` and `wifi_cipher_type_t`.
- `@W` flags: bit 0 hidden SSID, bit 1 WPS, bit 2 802.11b, bit 3 802.11g, bit 4 802.11n, bit 5 802.11a, bit 6 802.11ac, bit 7 802.11ax, bit 8 FTM responder.
- `@B` flags: bit 0 connectable, bit 1 scannable, bit 2 the line is a scan response. A scan response is reported on its own line, with bits 1 and 2 set; bit 0 is then unknown and 0. The host merges the lines of one address. `manufacturer id` and `name` are those of the line's own advertising data (the complete name if there is one, else the short name, at most 64 bytes).

### What gets reported

The node remembers up to 2048 networks and 1024 Bluetooth devices, each with the strongest and weakest RSSI it has reported. A sighting is reported when the device is new, stronger than any reported RSSI, weaker than any reported RSSI, or last reported 30 s ago or more. A Bluetooth sighting that carries a name is also reported when no name was reported for that device yet. A sighting that cannot be queued is counted as dropped and is not remembered, so it is reported the next time it is heard. When the table is full, the entry reported longest ago is dropped. A new plan, `#START` and a reboot clear the table.

## Host to node

| Command | Arguments | Meaning |
| --- | --- | --- |
| `#HELLO` | | send `@H` now |
| `#PLAN` | version (1 or more), country, 2.4 GHz bitmap (hex), 5 GHz bitmap (hex), active min ms (0 to 1500), active max ms (1 to 1500, not below min), passive ms (1 to 1500), BLE (`0` or `1`) | the channels to scan. The bitmaps are ESP-IDF 5.5 `wifi_scan_channel_bitmap_t` (`ghz_2_channels`, bit n = channel n, bit 0 skips the band; `ghz_5_channels`, bit 1 = 36 to bit 28 = 177, bit 0 skips the band). A plan takes effect at the next sweep (BLE at once) and is kept until reboot. The reply's detail is the version; a refused plan names the first bad field: `args`, `version`, `country`, `bitmap`, `time` or `ble`. |
| `#START` | | scan with the current plan. Without a plan the node scans every channel its country allows (the country of the last plan, kept on the node, else `01`), active 30 to 80 ms, passive 110 ms, without BLE |
| `#STOP` | | stop scanning |
| `#IDENT` | seconds (1 to 60, 5 if left out) | blink the status LED. The reply's detail is the seconds, or `seconds` when refused |
| `#LED` | `none`, `gpio` or `ws2812`, GPIO number, `1` if active low | the status LED, saved on the node. GPIO 0 to 10 and 23 to 28 are allowed (11 to 22 are UART0, USB, flash and PSRAM). Active low is optional, `0` if left out, and ignored for `ws2812`. `none` needs no other argument. A refusal names `args`, `kind`, `gpio` or `active_low`, or `storage` when it could not be saved |
| `#BOOTLOADER` | | restart into the ROM download mode, for flashing |
| `#REBOOT` | | restart |

- Country is two upper-case characters, one that ESP-IDF 5.5 knows: `01` (worldwide: channels 1 to 11 and 36 to 64), AT, AU, BE, BG, BR, CA, CH, CN, CY, CZ, DE, DK, EE, ES, FI, FR, GB, GR, HK, HR, HU, IE, IN, IS, IT, JP, KR, LI, LT, LU, LV, MT, MX, NL, NO, NZ, PL, PT, RO, SE, SI, SK, TW or US. The node sets it with 802.11d off, so the channels allowed are that country's. Any other country is refused with `@A,...,PLAN,err,country`.
- DFS channels (52 to 144) and channels 12 to 14 and 169 to 177 are always scanned passively; the others actively.
- While running, the node stops by itself when it has heard no host line for 30 s, and says so in an `@L`. The host sends `#HELLO` every 10 s. `#HELLO` is answered with `@A` and then `@H`.
- On a board without PSRAM the node may not have the memory for Bluetooth next to a full Wi-Fi table. It then says so in an `@L` and scans Wi-Fi only.
- After a reset caused by a crash, a watchdog, a brown-out or a CPU lock-up, the node's first `@L` after boot is `reset: panic`, `reset: watchdog`, `reset: brownout` or `reset: lockup`.
- `#BOOTLOADER` sets the ESP32-C5 force-download bit (`LP_AON_SYS_CFG_REG`, field `FORCE_DOWNLOAD_BOOT` = 1) and restarts, after `@A`. If the ROM does not answer within 2 s, the host falls back to the usual DTR/RTS reset into download mode, which boards with a USB bridge support, or asks for the BOOT button.
- Unknown commands get `@A,...,<command>,err,unknown`.

## Example

```
#PLAN,3,US,0842,1e,30,80,110,0*77
@A,17,PLAN,ok,3*63
#START
@A,18,START,ok,*0C
@W,19,240,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65*7A
@S,20,51234,4,1012,57,0,0,3,180224,run*03
```
