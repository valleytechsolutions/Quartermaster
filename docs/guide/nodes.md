# Nodes

[Back to the guide](README.md)

A **node** is a small ESP32-C5 board running the Quartermaster Node firmware. It scans Wi-Fi on 2.4 and 5 GHz (and Bluetooth if you ask) beside your adapters. Quartermaster shares the channels out between the nodes, so with more nodes each channel is heard more often. Nodes have no GPS and keep no logs: Quartermaster adds its own position and writes the log.

You can use up to **8 nodes on USB**, and up to **8 wireless nodes** through one hub.

## Supported boards

- **Seeed XIAO ESP32-C5**
- **Espressif ESP32-C5-DevKitC-1**, with a chip of revision v1.0 or later. Early boards carry a v0.1 sample chip, which cannot run the node firmware.
- **M5Stack Stamp-C5**

Other ESP32-C5 boards with 4 MB of flash or more may work too.

## Step 1: put the firmware on the board (flashing)

### From the Cardputer

1. Stop any wardrive or Recon. Flashing restarts the board.
2. Plug the board in.
3. Open **Settings > Flash a node** and pick the board.
   - A board without the node firmware gets **Install**. This erases the board and takes about a minute.
   - A node you already have can choose **Update**, which keeps its settings, or **First install**, which erases it.
4. A progress bar shows how far it is.

A node whose firmware is older than the one that came with Quartermaster shows **Old firmware**, and **Update firmware** on its own screen in **Settings > Nodes**.

### From a computer or an Android phone

The [flasher web page](https://valleytechsolutions.github.io/Quartermaster/flasher/) works in Chrome or Edge on a computer, or Chrome on Android. Safari and every browser on iPhone and iPad cannot flash a board.

1. Plug the board into the computer or phone.
2. Press **First install** for a new board, or **Update, keep settings** for a board that already runs Quartermaster Node.
3. Pick the board in the list.

### By hand

If you use the ESP32 tools yourself, write the merged image at offset 0:

```sh
esptool.py --chip esp32c5 write_flash 0x0 quartermaster-node-1.1.0-merged.bin
```

The image is in [docs/flasher](../flasher/). More details are in [firmware/README.md](../../firmware/README.md).

### If the board does not answer

Put it in download mode and try again:

- **Seeed XIAO ESP32-C5:** hold BOOT while you plug it in.
- **M5Stack Stamp-C5:** hold G28 to GND while you plug it in.
- **ESP32-C5-DevKitC-1:** hold BOOT and press RESET.

Messages you may see while flashing:

| Message | What to do |
| --- | --- |
| No ESP32-C5 answered. Hold BOOT, plug the board in again, and retry. | Use download mode, above. |
| This is not an ESP32-C5. | Use an ESP32-C5 board. |
| The board answered but would not get ready for flashing. Plug it in again and retry. | Plug it in again and retry. |
| A part of Quartermaster is missing (python3-serial). Reinstall Quartermaster. | Reinstall with `apt` (see [Install](install.md)). |
| The node firmware does not fit on this board. | Use a board with more flash. |
| The flash chip of this board did not answer. | Plug it in again and retry. If it stays, the board may be faulty. |
| The board's port could not be opened. / The board stopped answering. | Plug it in again and retry. |
| Something else is using this board's port. | Plug it in again and retry. |
| This ESP32-C5 is an early sample (revision ...) and cannot run the node firmware. | This board cannot be a node. |
| This ESP32-C5 is a newer revision (...) than the node firmware knows. Update Quartermaster. | Update Quartermaster. |
| This board has ... of flash. A node needs 4 MB or more. | Use a board with more flash. |
| This ESP32-C5 is locked against flashing (secure download mode). | This board cannot be flashed. |
| This node was set up another way. Use a first install, which also clears its settings. | Press **OK** for a first install. |
| Writing the firmware failed. Plug the board in again and retry. / The firmware on the board did not check out after writing. Retry. | Plug it in again and retry. |
| The node firmware that came with Quartermaster is damaged. Reinstall Quartermaster. | Reinstall. |

## Step 2: add the node

1. Plug the board in. Quartermaster says **New node found. Settings > Nodes to add it.**
2. Open **Settings > Nodes** and press **OK** on the new node.
3. It scans in every wardrive and in Recon from then on.

The first time you open **Settings > Nodes**, Quartermaster asks for your country. The country decides which Wi-Fi channels the nodes may use. Until you choose, it uses United States.

If gpsd is installed, plug a node in by its own USB port, not a serial (UART) port, because gpsd may take a serial port for a GPS.

## A node's screen

Press **OK** on a node in **Settings > Nodes**:

| Row | What it does |
| --- | --- |
| Name | The name shown in lists and on the Hardware screen. |
| Bluetooth | Lets the node scan Bluetooth too. This halves its time for Wi-Fi. |
| Light | Which light the board has. A node uses no light until you choose one. |
| Identify | Blinks its light for 5 seconds, to tell it from the others. |
| Hub for wireless nodes | Makes this node the hub (see below). |
| Update firmware | Shows when the node's firmware is older than Quartermaster's, and only while the node is plugged in by USB. A wireless node must be plugged in first. Settings stay. |
| Remove | Quartermaster stops using it. Press **OK** on it in Nodes to add it back. |

The Hardware screen has a card for each node with the channels it scans.

## Wireless nodes

Nodes do not have to be plugged in. One node stays on the Cardputer as the **hub**. Up to 8 more run on a battery or a USB charger and send what they hear to the hub by radio.

1. Open the node that stays plugged in and turn on **Hub for wireless nodes**. The hub does not scan itself. There is one hub: turning it on for one node turns it off for the others. It needs node firmware 1.1.0 or later.
2. In **Settings > Nodes**, choose **Add a wireless node**. Pairing stays open for 60 seconds. Press **OK** to open it again.
3. Power the new node from a battery or a USB charger, **not from the Cardputer**, and hold its **BOOT** button for 2 seconds (on a Stamp-C5, hold G28 to GND). It shows up as **New wireless node**. Press **OK** to add it.

   On a Stamp-C5 whose **Light** is set to G28, this button no longer works, so holding it cannot add the node. Quartermaster warns you before you pick G28. To add such a node, plug it in and set its **Light** to something other than G28 first. (People with a serial terminal can instead send it `#UNPAIR` while it is plugged in. See the [node firmware notes](../../firmware/README.md).)

Good to know:

- **Power:** wireless nodes take no power from the Cardputer. This is the easiest way to add scanning without a bigger USB hub. See [Power](power.md).
- Wireless nodes start and stop with every wardrive and Recon.
- A node not heard for 30 seconds says "Not heard for 30 s. Out of range or off?" and hands its channels to the others until it is heard again.
- When the hub is unplugged, wireless nodes wait for it ("Waiting for the hub. Plug it in.") and the USB nodes scan every channel.
- A wireless node is somewhere within radio range of the hub, not at the Cardputer. Its rows get the Cardputer's position, with an accuracy 50 m wider, and are never used as a device's furthest row.
- Removing a wireless node makes the hub forget it. Hold BOOT on it for 2 seconds to add it again.
- Moving the hub to another node means adding every wireless node again. Quartermaster asks first.

## Node messages

| Message | What to do |
| --- | --- |
| Needs a firmware update / Old firmware | Update it from its screen or with Flash a node. |
| Update Quartermaster to use this node | The node's firmware is newer than Quartermaster. Update Quartermaster. |
| Stopped answering. Plug it in again. | Replug it. |
| Not plugged in | It is in your list but not connected. |
| Did not take its channels | Replug it. If it stays, flash it again. |
| No Bluetooth: the node is short of memory | Boards without extra memory (PSRAM), such as the Stamp-C5, may scan Wi-Fi only. |
| Quartermaster takes up to 8 nodes / The hub takes up to 8 wireless nodes | Remove one first. |
| No hub. Make a plugged-in node the hub. | Turn on Hub for wireless nodes on a USB node. |
| Not on this hub. Add it again. | Hold BOOT for 2 seconds and add it again. |
| Needs a firmware update to be a hub | Update the node's firmware. |
| Could not start as a hub. Plug it in again. | Replug it. |
