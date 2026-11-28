# Quartermaster Node serial protocol, version 1

A Quartermaster Node is an ESP32-C5, C3, C6 or S3 that scans Wi-Fi (2.4 and 5 GHz on the C5, 2.4 GHz on the others) and, when asked, Bluetooth LE, and reports what it hears to Quartermaster over USB. It keeps no logs and has no GPS: Quartermaster stamps every sighting with its own fix.

## Link

- The node writes every line to the native USB Serial/JTAG port and to UART0 (TX/RX GPIO 11/12 on the C5, 21/20 on the C3, 16/17 on the C6, 43/44 on the S3) at 921600 baud, 8N1, so either USB port of a board works. It reads commands from both.
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

- `node id` is lower-case hex. `chip revision` is major.minor. `board` is the node's own guess: on the C5 `stamp-c5` (4 MB flash inside the chip package, no PSRAM), `c5-8mb` (8 MB flash) or `c5` (anything else, such as a 4 MB board with external flash); on the other chips `c3`, `c6` or `s3`. `capabilities` is a `+` list: `wifi24`, `wifi5` (C5 only), `ble`; `ble` is left out after the node has refused Bluetooth for lack of memory. `state` is `idle` or `run`. `plan version` is the version of the plan in use, `0` before the first. `country` is the country in use.
- `age ms` is how long before the line was written the sighting happened. For Wi-Fi it is counted from the middle of the scan that heard the network, so it is off by at most half a scan (one scan covers either the active or the passive channels of a sweep).
- `networks reported` and `devices reported` count the entries added to the table since it was last cleared; a device that was dropped from a full table and comes back counts again.
- `auth mode`, `pairwise cipher` and `group cipher` are the numbers of ESP-IDF 5.5 `wifi_auth_mode_t` and `wifi_cipher_type_t`.
- `@W` flags: bit 0 hidden SSID, bit 1 WPS, bit 2 802.11b, bit 3 802.11g, bit 4 802.11n, bit 5 802.11a, bit 6 802.11ac, bit 7 802.11ax, bit 8 FTM responder.
- `@B` flags: bit 0 connectable, bit 1 scannable, bit 2 the line is a scan response. A scan response is reported on its own line, with bits 1 and 2 set; bit 0 is then unknown and 0. The host merges the lines of one address. `manufacturer id` and `name` are those of the line's own advertising data (the complete name if there is one, else the short name, at most 64 bytes).

### What gets reported

The node remembers up to 2048 networks and 1024 Bluetooth devices, each with the strongest and weakest RSSI it has reported. A sighting is reported when the device is new, stronger than any reported RSSI, weaker than any reported RSSI of -92 dBm or more, or last reported 30 s ago or more. Weaker sightings below -92 dBm are not reported again. A Bluetooth sighting that carries a name, or a manufacturer id, is also reported when none was reported for that device yet. A sighting that cannot be queued is counted as dropped and is not remembered, so it is reported the next time it is heard. While running, the node waits for the host to read each line for as long as the USB port is connected, rather than dropping lines it already queued; new sightings that do not fit in the queue meanwhile are the ones dropped. When the table is full, the entry reported longest ago is dropped. A new plan, `#START` and a reboot clear the table.

## Host to node

| Command | Arguments | Meaning |
| --- | --- | --- |
| `#HELLO` | | send `@H` now |
| `#PLAN` | version (1 or more), country, 2.4 GHz bitmap (hex), 5 GHz bitmap (hex), active min ms (0 to 1500), active max ms (1 to 1500, not below min), passive ms (1 to 1500), BLE (`0` or `1`) | the channels to scan. The bitmaps are ESP-IDF 5.5 `wifi_scan_channel_bitmap_t` (`ghz_2_channels`, bit n = channel n, bit 0 skips the band; `ghz_5_channels`, bit 1 = 36 to bit 28 = 177, bit 0 skips the band). A plan takes effect at the next sweep (BLE at once) and is kept until reboot. The reply's detail is the version; a refused plan names the first bad field: `args`, `version`, `country`, `bitmap`, `time` or `ble`. |
| `#START` | | scan with the current plan. Without a plan the node scans every channel its country allows (the country of the last plan, kept on the node, else `01`), active 30 to 80 ms, passive 110 ms, without BLE |
| `#STOP` | | stop scanning |
| `#IDENT` | seconds (1 to 60, 5 if left out) | blink the status LED. The reply's detail is the seconds, or `seconds` when refused |
| `#LED` | `none`, `gpio` or `ws2812`, GPIO number, `1` if active low | the status LED, saved on the node. Allowed GPIOs: C5 0 to 10 and 23 to 28 (11 to 22 are UART0, USB, flash and PSRAM); C3 0 to 10; C6 0 to 11, 14, 15 and 18 to 23; S3 0 to 18, 21, 38 to 42 and 45 to 48. Active low is optional, `0` if left out, and ignored for `ws2812`. `none` needs no other argument. A refusal names `args`, `kind`, `gpio` or `active_low`, or `storage` when it could not be saved |
| `#BOOTLOADER` | | restart into the ROM download mode, for flashing |
| `#REBOOT` | | restart |

- Country is two upper-case characters, one that ESP-IDF 5.5 knows: `01` (worldwide: channels 1 to 11 and 36 to 64), AT, AU, BE, BG, BR, CA, CH, CN, CY, CZ, DE, DK, EE, ES, FI, FR, GB, GR, HK, HR, HU, IE, IN, IS, IT, JP, KR, LI, LT, LU, LV, MT, MX, NL, NO, NZ, PL, PT, RO, SE, SI, SK, TW or US. The node sets it with 802.11d off, so the channels allowed are that country's. Any other country is refused with `@A,...,PLAN,err,country`.
- DFS channels (52 to 144) and channels 12 to 14 and 169 to 177 are always scanned passively; the others actively.
- While running, the node stops by itself when it has heard no host line for 30 s, and says so in an `@L`. The host sends `#HELLO` every 10 s. `#HELLO` is answered with `@A` and then `@H`.
- On a board without PSRAM the node may not have the memory for Bluetooth next to a full Wi-Fi table. It then says so in an `@L` and scans Wi-Fi only.
- After a reset caused by a crash, a watchdog, a brown-out or a CPU lock-up, the node's first `@L` after boot is `reset: panic`, `reset: watchdog`, `reset: brownout` or `reset: lockup`.
- `#BOOTLOADER` sets the chip's force-download bit (`LP_AON_SYS_CFG_REG` `FORCE_DOWNLOAD_BOOT` on the C5 and C6, `RTC_CNTL_OPTION1_REG` `FORCE_DOWNLOAD_BOOT` on the C3 and S3) and restarts, after `@A`. If the ROM does not answer within 2 s, the host falls back to the usual DTR/RTS reset into download mode, which boards with a USB bridge support, or asks for the BOOT button.
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

## Wireless nodes

Firmware 1.1.0 and later adds wireless nodes. Nothing above changes; this section only adds lines and commands. One firmware image can work in three roles:

- **USB node**: everything above. Every node starts in this role after a restart.
- **Hub**: a node plugged into the host that the host has sent `#HUB`. It does not scan. It stays on its home channel, relays the lines of its air nodes to the host and passes the host's commands to them.
- **Air node**: a node with no host, on a battery or a USB charger. It scans like a USB node and sends its lines to its hub by radio (ESP-NOW on the 2.4 GHz home channel).

A hub has at most 17 air nodes (firmware 1.1.x: 8). An air node belongs to one hub. Since firmware 1.2.0 an air node that cannot reach its hub can reach it through other air nodes of the same hub (see "Relaying").

### Roles

- A node that hears no host line in its first 10 s without a computer on its USB port becomes an air node. While a computer is connected to the USB port the node waits for the host, however long it takes. `#AIR` makes it an air node at once.
- Any host line on either serial port turns an air node back into a USB node before the line is carried out, so the flasher and `#HELLO` always work. A node that has heard a host line since it started does not become an air node by itself again until it restarts.
- The role is not saved. A hub saves its key and its list of admitted nodes; an air node saves its hub, the key and the home channel.
- `@H` is unchanged. Its `capabilities` also list `espnow` on firmware that can be a hub or an air node.
- `@C`, `@N` and `@J` lines count in `seq` like `@S`. `@R` lines carry no `seq` of the hub.

### Host to hub

| Command | Arguments | Meaning |
| --- | --- | --- |
| `#HUB` | home channel (1 to 11, 6 if empty), cluster key (32 hex digits, not all zero) | become a hub. The reply's detail is the channel; it is followed by `@C` and `@N`. A refusal names `args`, `channel`, `key`, `radio` or `memory`. If the key is the one the node has saved, its admitted nodes are kept; otherwise the new key is saved and the list is cleared, so every air node has to be admitted again: first clear each air node's pairing (`#UNPAIR` over USB, or hold BOOT), so it asks to join. Send it each time the port is opened; it is harmless when nothing changed. A scan in progress is stopped. |
| `#USB` | | stop being a hub: no more relaying. A USB node answers `ok` and does nothing. |
| `#UNPAIR` | | any node, in any role: clear this node's own pairing with a hub, as holding BOOT does. Replies `ok`. As an air node later it asks to join again. |
| `#AIR` | | become an air node now. The `@A` is written first; after it the node writes nothing more to its serial ports, and the next host line makes it a USB node again. |
| `#PAIR` | seconds (0 to 600, 60 if left out) | hub only. Open a pairing window for that long; `0` closes it. The reply's detail is the seconds; it is followed by `@C` and `@N`. A refusal names `role` or `seconds`. |
| `#ADMIT` | node id | hub only. Admit a node that asked to join (`@J`) in the last 30 s while the window was open. This also admits Dual Band Wardriver and Piglet nodes (see "Other wardriver nodes"). The hub hands it the key; the node then shows in `@N` as `new` and, once heard, `up`. The window stays open at least 30 s more. Admitting a node that is already admitted hands it the key again (after a reset of the node). If joins with two different keys arrive under one node id while the window is open, the hub stops answering that node id and refuses to admit it with `node` until the window has closed and is opened again. The reply's detail is the node id in lower case. A refusal names `role`, `node` (not asking to join), `full` (17 nodes) or `radio`. |
| `#FORGET` | node id | hub only. Remove a node. The hub tells the node at its next contact within 60 s; the node then forgets its hub and asks to join again. A node that does not get in touch within those 60 s keeps its pairing and searches for ever: clear it with `#UNPAIR` over USB or by holding BOOT. The reply's detail is the node id in lower case. A refusal names `role` or `node`. |
| `#TO` | node id, command, the command's arguments | hub only. Pass a command to an air node: `#TO,a0b1c2d3e4f5,IDENT,5` carries `#IDENT,5`. Allowed: `HELLO`, `PLAN`, `IDENT`, `LED` and `REBOOT`. The checksum, if any, is for the whole `#TO` line. `ok` means queued and its detail is the node id in lower case; the node's own `@A` arrives later in an `@R`. Up to 4 commands wait per node; they are delivered in order, each once, at the node's next contact (within about 1 s while it scans, 2 s while idle). A refusal names `role`, `args`, `node`, `command` or `busy` (4 waiting). |

- `node id` is the air node's own node id, as in its `@H`: 12 lower-case hex digits (upper case is accepted from the host).
- On a hub, `#START` and `#STOP` start and stop all its air nodes; the hub itself scans nothing. `#PLAN` is refused with `err,hub`: send a plan to each air node with `#TO`. `#HELLO` is answered with `@A`, `@H`, `@C` and one `@N` per admitted node. `#IDENT`, `#LED`, `#BOOTLOADER` and `#REBOOT` work on the hub itself.
- The 30 s host-silence stop applies to a hub; its air nodes then stop too.
- `#PAIR`, `#ADMIT`, `#FORGET` and `#TO` on a node that is not a hub are refused with `err,role`.

### Hub to host

| Line | Fields | When |
| --- | --- | --- |
| `@C` | seq, home channel, pairing seconds left (`0` when closed), admitted nodes, state (`run` or `idle`), lines relayed, lines dropped | every 5 s, after `#HUB` and `#PAIR`, and in reply to `#HELLO` |
| `@N` | seq, node id, link (`new`, `up` or `lost`), heard ms ago, link RSSI, frames, frames resent, via, hops | one per admitted node right after each `@C`, and when a newly admitted node is first heard |
| `@J` | seq, node id, link RSSI, firmware version, board | a node asks to join while the pairing window is open; at most every 5 s per node |
| `@R` | node id, then one line of that air node without its checksum | every line an air node sends |

- `@C`: `lines relayed` counts the `@R` lines since the hub started; `lines dropped` is the hub's own count (as in its `@S`). When the hub has no room left to queue an air node's lines for the serial port, it does not take them: it answers that it is busy and the air node keeps them and sends them again.
- `@N`: `new` is admitted but not heard yet; `up` was heard in the last 60 s; `lost` was not, or was not heard since `#HUB`. `heard ms ago` and `link RSSI` (dBm, of the node's last frame at the hub) are empty when it has not been heard. `frames` counts the frames of new lines from the node since `#HUB`, `frames resent` the frames it got again because the node missed the hub's answer.
- `@N` `via` (firmware 1.2.0 and later) is the node id of the air node the node is heard through, empty when the hub hears it itself; `hops` is `1` when it is heard directly, else the number of radio hops to the hub. With `via` set, `link RSSI`, `frames` and `frames resent` are those of the last hop, as the relaying node sees them. Firmware 1.1.x sends `@N` without these two fields.
- `@J`: show it as a new wireless node, which `#ADMIT` adds. `firmware version` and `board` are the node's own, as in its `@H`, at most 8 characters each; a comma, `*` or a byte outside printable ASCII is sent as `_`.

### `@R` lines

An `@R` line is `@R,<node id>,<line>*hh` where `<line>` is the air node's line from its `@` up to, not including, its `*`, and `hh` is the checksum of the whole `@R` line by the usual rule (the XOR of every byte after the first `@` and before the last `*`). An `@R` line is at most 240 bytes like any line: air nodes only send lines of up to 210 bytes. For example:

```
@R,a0b1c2d3e4f5,@W,9,1740,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65*6B
```

To read it, take the text after the second comma up to the last `*`, add `*`, the XOR of that text after its `@` as two upper-case hex digits, and `\n`. That gives a line in the version 1 format, here `@W,9,1740,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65*7F`, which is read by the rules above as a line of that node.

- The inner lines are the air node's `@H`, `@W`, `@B`, `@S`, `@A` and `@L` lines. Their `seq` is the air node's own; gaps in it count the lines lost from that node.
- In `@W` and `@B` lines the hub has added to `age ms` the time the line waited on the air node, so `age ms` counts to when the hub received it. Relaying adds a few ms more.
- An air node is somewhere within radio range of the hub, not at the hub. A sighting it reports was made where the air node is.
- An air node's `@S` `lines dropped` also counts the lines it gave up on: a frame is given up when the hub's radio took it 6 times and no answer came back. Tries the hub's radio did not take do not count, so while the hub is out of reach lines wait in the buffer. Its `@H` and `@S` are only sent while it is in touch with its hub.
- Lines in `@R` can arrive late (an air node keeps them while it cannot reach the hub) and in bursts.

### Air nodes

- **Unpaired** (new, or after its pairing was cleared): it asks to join on channels 1 to 11 in turn, 150 ms each. A hub answers only while its pairing window is open; the node then stays on that channel and asks once a second until it is admitted, or until it has not heard the hub for 10 s. The LED blinks fast.
- **Searching**: it tries its hub on the saved home channel 3 times, then on channels 1 to 11, and again. The LED gives a short flash every 2 s. When the hub answers on another channel, that channel is saved.
- **In touch**: it follows the hub's state, which comes with every answer of the hub. When the hub runs it scans with its plan (without one, every channel of its country, as for `#START`); when the hub is idle it stops. While scanning it hands its lines to the hub after every few channels (about 0.6 s of scanning) and the LED blinks briefly at each handover. While idle it checks in every 2 s.
- If the hub has not answered for 60 s, it stops scanning, says `wireless: no hub for 60 s, stopped` in an `@L` that is relayed later, and searches. An air node does not stop after 30 s without a host line.
- A plan is kept until the node restarts. After a restart the relayed `@H` shows plan version `0`; send the plan again.
- An air node refuses commands other than `HELLO`, `PLAN`, `IDENT`, `LED` and `REBOOT` with `err,air`. After `#REBOOT` it restarts once its reply has reached the hub, or after 3 s.
- It keeps its lines in a buffer (32 KB with PSRAM, 16 KB without; 8 KB before 1.2.0) until the hub has them. Lines it carries for other air nodes go in the same buffer. When the buffer is full, new lines are dropped and counted in `lines dropped`.
- Holding the BOOT button (GPIO 28 on the C5, 9 on the C3 and C6, 0 on the S3) for 2 s clears the pairing on any node, in any role, and says `wireless: pairing cleared` in an `@L`. An air node then asks to join again. A hold only counts once the button has been seen up since the node started, and the button is not read while the status LED is set to GPIO 28. `#UNPAIR` over USB does the same.

### Radio

For reference; the host never sees these frames. Every frame starts with `Q`, the version `1` and the frame type; numbers are little-endian. Addresses are the Wi-Fi station addresses, which are the node ids.

| Type | From | Sent | Body |
| --- | --- | --- | --- |
| 1 join | air node | broadcast, plain | firmware version (8 bytes, NUL padded), board (8), the node's P-256 public key (65, uncompressed) |
| 2 wait | hub | broadcast, plain | node address (6), home channel (1), pairing seconds left (2) |
| 3 admit | hub | broadcast, plain | node address (6), home channel (1), the hub's one-off P-256 public key (65), wrapped key (16), check (8) |
| 4 data | air node | to the hub, encrypted | session (4, random at each start), frame seq (4), node clock ms (4), last command id run (1), flags (1: bit 0 command id valid, bit 1 poll, bit 2 scanning, bit 3 can relay), lines (1), then per line: length (1), node clock ms when the line was written (4), the line without its checksum |
| 5 answer | hub | to the node, encrypted | session (4), frame seq (4), flags (1: bit 0 run, bit 1 forgotten, bit 2 answers a poll, bit 3 busy, bit 4 relaying understood), then the relay section when bit 4 is set (see "Relaying"), commands (1), then per command: id (1), length (1), the command line without a checksum |
| 6 bye | hub | to the node, encrypted | nothing |
| 7 offer | air node | broadcast, plain | hub address (6), hops from the hub (1), RSSI of its own link towards the hub (1, signed), ms until it next listens (2, 0 when idle), tag (8) |
| 8 ask | air node | broadcast, plain | hub address (6) |

- Joining: both sides compute the ECDH secret of the node's key and the hub's one-off key. `kek` = SHA-256(`qm-join` ‖ secret ‖ node address ‖ hub address). The wrapped key is the cluster key XOR the first 16 bytes of `kek`; the check is the first 8 bytes of HMAC-SHA-256 with the last 16 bytes of `kek` as key over the cluster key. A node only takes a key whose check is right.
- After joining, frames are encrypted by ESP-NOW (CCMP). The primary master key is the first 16 bytes of HMAC-SHA-256(cluster key, `qm-pmk`); the key of each node's link is the first 16 bytes of HMAC-SHA-256(cluster key, `qm-lmk` ‖ node address). The hub and each node only take data and answers from each other.
- A data frame is up to 1400 bytes (ESP-NOW version 2), or 250 if the radio refuses longer ones. Each try waits 40 ms for the answer, and a handover lasts at most about 150 ms. A frame is given up after 6 tries that the hub's radio took without an answer; tries it did not take do not count. The hub answers every data frame, passes new frames on and drops frames it already has. When it has no room to queue a new frame's lines for the host, it answers with bits 2 and 3 set and does not take the frame; the node keeps the lines, ends the handover and sends them again at the next one. Such answers do not count as tries.
- A command stays in the hub's answers until the node reports its id as run. After `#HUB` the hub numbers a node's commands on from the id in the node's first frame, so commands queued before that frame are not taken as already run.

### Relaying

Firmware 1.2.0 and later. An air node that has not had an answer from its parent (the hub, or the air node it goes through) for 10 s asks the hub once more, then broadcasts an ask on the home channel. Every air node of the same hub that is in touch with it, fewer than 3 hops from it, and not itself below the asker, answers with an offer. The tag of an offer is the first 8 bytes of HMAC-SHA-256 with the cluster key over `qm-offer` ‖ the offering node's address ‖ the hub address ‖ hops, so only members of the cluster are believed. The asker takes the offer with the fewest hops, and of those the strongest weakest link, and from then on sends its data frames to that node instead of the hub. While it goes through another node it tries the hub itself every 30 s and goes back to it when the hub answers.

- A relaying node keeps up to 4 nodes below it and learns the nodes below those from the lines they carry. It answers their data frames itself, takes their lines into its own buffer as `@R,<node id>,<line>` (a line already in that form keeps its node id, so a line is wrapped once however many hops it takes), and tells the hub about each of them every 5 s, and as soon as it first hears one, with an `@V` line: `@V,<node id>,<heard ms ago>,<link RSSI>,<frames>,<frames resent>,<last command id run>` (the last field is empty until the node has reported one).
- Every node of 1.2.0 or later sets bit 3 in its data frames. A hub of 1.2.0 or later then sets bit 4 in its answer and adds a relay section to it. A node only offers to relay while its own parent's answers have bit 4, so under a hub of 1.1.x nothing is relayed. The relay section is: ms until the relaying node should hand over next (2, `0` from the hub), the number of entries (1), then per node reached through it: address (6), kind (1: `0` commands, `1` forgotten), commands (1), then per command id (1), length (1) and the command line. The relaying node passes each entry on to the next node down in its own answers. A node that is forgotten gets bit 1 in the answer from its relaying node, as from the hub.
- The hub reads `@V` lines (not passed to the host) to fill in `via`, `hops`, `heard ms ago` and the counters of `@N`, and to know which commands a relayed node has run. It drops `@R` lines of node ids that are not admitted.
- A relaying node with nodes below it listens on the home channel for 120 ms after each of its handovers, else for 60 ms after every 4th. Nodes below time their handovers to those windows from the ms they were given.
- ESP-NOW encryption uses the same keys as a direct link: every node's link key is HMAC-SHA-256(cluster key, `qm-lmk` ‖ that node's address), whichever node it talks to.

### Other wardriver nodes

Firmware 1.2.0 and later. A hub on home channel 6 also takes nodes of the [ESP32 Dual Band Wardriver](https://github.com/justcallmekoko/ESP32DualBandWardriver) (node mode, without encryption) and [Piglet](https://github.com/Hamspiced/piglet) mesh nodes. They speak their own protocol, on channel 6: frames that start with `ENOW`.

- While pairing is open, such a node's `CORE_REQUEST` or `HEARTBEAT` gives an `@J` with firmware `jcmk`, `piglet` (its request carries `PIGLET1`) or `jcmk6g` (an external 6 GHz node) and board `c5`. `#ADMIT` adds it without a key exchange; it counts towards the 17.
- Once added, the hub answers its `CORE_REQUEST` with `CORE_REPLY`, and its `CORE_REQUEST` and `HEARTBEAT` with an `ADMIN` that gives it a run of its channel list, once it has a plan.
- The hub writes lines for it as if it were an air node: `@N` (hops `1`), and `@R` with an `@H` (firmware as above, chip `0.0`, flash and PSRAM `0`, board `c5`, capabilities `wifi24+wifi5+block`, or `wifi6+block` for a 6 GHz node) every 10 s while it is heard, an `@S` every 5 s while running, and an `@W` or `@B` for each record it sends while the hub runs. Auth mode and cipher come from its security text; flags and BLE address type are `0`; `age ms` is `0`.
- `#TO` with `HELLO` or `PLAN` works; other commands get `@A,...,err,jcmk`. A plan is turned into the smallest run of its channel list (1 to 14, 36 to 64, 100, 112 to 144, 149 to 177) that holds every channel of the plan. Quartermaster gives `block` nodes one run each and never 104 or 108, which these nodes do not scan.
- They cannot relay or be relayed.

### Example

```
#HUB,6,8f1e2d3c4b5a69788796a5b4c3d2e1f0
@A,21,HUB,ok,6*2F
@C,22,6,0,0,idle,0,0*5D
#PAIR,120
@A,23,PAIR,ok,120*7D
@C,24,6,120,0,idle,0,0*58
@J,25,a0b1c2d3e4f5,-48,1.1.0,c5-8mb*3A
#ADMIT,a0b1c2d3e4f5
@A,26,ADMIT,ok,a0b1c2d3e4f5*12
@N,27,a0b1c2d3e4f5,up,3,-47,1,0,,1*49
@R,a0b1c2d3e4f5,@H,1,a0b1c2d3e4f5,1.1.0,1.0,8,8,c5-8mb,wifi24+wifi5+ble+espnow,US,0,idle*45
#TO,a0b1c2d3e4f5,PLAN,3,US,0842,1e,30,80,110,0
@A,28,TO,ok,a0b1c2d3e4f5*52
@R,a0b1c2d3e4f5,@A,5,PLAN,ok,3*44
#START
@A,29,START,ok,*0E
@R,a0b1c2d3e4f5,@W,9,1740,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65*6B
```
