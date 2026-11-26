<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/brand/quartermaster-logo-dark.png">
    <img src="assets/brand/quartermaster-logo.png" width="150" alt="Quartermaster logo: a brass spyglass">
  </picture>
</p>

<h1 align="center">Quartermaster</h1>

<p align="center">
  <b>The wardriver for the M5Stack CardputerZero and DIY Raspberry Pi handhelds.</b><br>
  Wi-Fi, Bluetooth, nodes, aircraft and LoRa mesh. Every radio at once.<br>
  Clean WiGLE logs. Automatic uploads. Drive Alerts. Recon.
</p>

<p align="center">
  <a href="https://github.com/valleytechsolutions/Quartermaster/releases/latest"><img src="https://img.shields.io/github/v/release/valleytechsolutions/Quartermaster?color=f5b301&label=release" alt="Latest release"></a>
  <a href="https://github.com/valleytechsolutions/Quartermaster/actions/workflows/ci.yml"><img src="https://github.com/valleytechsolutions/Quartermaster/actions/workflows/ci.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-f5b301" alt="GPL-3.0 license"></a>
  <a href="#install"><img src="https://img.shields.io/badge/AppStore-share%20code%20WARD-f5b301" alt="On the CardputerZero AppStore: share code WARD"></a>
</p>

<p align="center">
  <img src="docs/screens/boot.gif" width="320" alt="Boot animation: a black and gold ship drops three sails">
  <img src="docs/screens/wardrive-mesh.png" width="320" alt="Live wardrive: 412 Wi-Fi networks, 187 Bluetooth devices, 23 aircraft and 9 mesh nodes">
</p>

<p align="center">
  <a href="docs/guide/README.md">User guide</a> ·
  <a href="#install">Install</a> ·
  <a href="https://github.com/valleytechsolutions/Quartermaster/wiki">User guide</a> ·
  <a href="#what-it-does">What it does</a> ·
  <a href="#your-first-wardrive">First wardrive</a> ·
  <a href="#nodes">Nodes</a> ·
  <a href="#aircraft">Aircraft</a> ·
  <a href="#mesh-nodes">Mesh nodes</a> ·
  <a href="#settings">Settings</a>
</p>

Plug in your radios and a GPS, press one key, and drive. Quartermaster finds your hardware by itself and scans on every radio at the same time. It counts each device once and writes logs in exactly the format WiGLE expects. When the Cardputer is back online, the logs go to WiGLE, WDGWars and Wardrift on their own.

## User guide

New to Quartermaster? The [user guide](docs/guide/README.md) explains everything step by step, in plain words:

- [Install and update](docs/guide/install.md) · [First start and the GPS](docs/guide/first-start.md) · [The wardrive screen and keys](docs/guide/wardrive.md) · [Settings](docs/guide/settings.md)
- [Wi-Fi adapters](docs/guide/wifi-adapters.md) · [Power and USB hubs](docs/guide/power.md) · [Getting the most networks](docs/guide/more-networks.md) · [Deep scan](docs/guide/deep-scan.md)
- [Drive Alerts and Recon](docs/guide/alerts.md) · [Nodes](docs/guide/nodes.md) · [Aircraft and LoRa mesh](docs/guide/aircraft-and-lora.md) · [Uploads](docs/guide/uploads.md)
- [Files and logs](docs/guide/files.md) · [Troubleshooting](docs/guide/troubleshooting.md) · [License and credits](docs/guide/license.md)

## Install

### Build a Raspberry Pi handheld

A Raspberry Pi with a 40-pin header can run Quartermaster with either of Raspyjack's Waveshare LCD HATs.
Choose the 1.44″ 128×128 ST7735S or the 1.3″ 240×240 ST7789. Both have a five-way joystick and three buttons.
The Pi package includes a native square dashboard, a joystick keyboard and an optional start-at-boot service.
The easiest start is a ready SD card image for your HAT: flash it with Raspberry Pi Imager and the Pi boots straight into Quartermaster.
Follow the [DIY build guide](docs/diy.md) for the parts, the ready images, a manual Raspberry Pi OS setup, and first-drive checks. [Download the Pi installer, the ready images and the checksums](https://github.com/valleytechsolutions/Quartermaster/releases/tag/pi-v1.2.9).

### From the CardputerZero AppStore

<p align="center">
  <img src="packaging/icon.png" width="88" alt="The Quartermaster app icon: look for it in the AppStore">
</p>

Quartermaster is on the CardputerZero AppStore. Open the AppStore app on the Cardputer and search for **Quartermaster**, or enter the share code **WARD**.

Updates come through the AppStore app too. It lists the apps with a newer version, and key **7** updates them.

For a fresh or replacement SD card, follow the [CardputerZero flashing and installation guide](https://github.com/valleytechsolutions/Quartermaster/wiki/CardputerZero-installation).

### With one command

New versions reach GitHub first, and the AppStore after its review. To get the newest one now, open a terminal on the CardputerZero, or connect to it with SSH, and paste this:

```sh
wget -qO /tmp/quartermaster.deb https://github.com/valleytechsolutions/Quartermaster/releases/latest/download/quartermaster.deb && sudo apt install -y /tmp/quartermaster.deb
```

The same command updates it. To remove it: `sudo apt remove quartermaster`. Your logs and settings stay where they are.

### After you install

Restart the Cardputer once after the first install, then open **Quartermaster** from the launcher. The restart lets Quartermaster reach an RTL-SDR stick and the Cap LoRa-1262.

**Settings > Check for updates** tells you when a newer version is out. It only asks the AppStore and GitHub, and never downloads anything.

## What it does

**Radios**

- **Every supported Wi-Fi adapter at once.** Built-in and any [supported USB adapter](#wi-fi-adapters), single, dual or tri-band, across 2.4, 5 and 6 GHz. On either USB port, through a hub or not. Plug one in or pull one out in the middle of a wardrive and it joins or leaves.
- **Bluetooth beside it.** The built-in radio logs Bluetooth LE and classic devices in every wardrive.
- **No adapter needed.** The built-in 2.4 GHz radio, Bluetooth and a GPS make a full wardriver.
- **Nodes.** Small ESP32-C5 boards scan Wi-Fi beside your adapters. Quartermaster shares the channels out between them.
- **Wireless nodes.** One node stays plugged in as a hub. Up to 8 more run on a battery and send what they hear to it by radio.
- **Aircraft.** Plug in an RTL-SDR stick and the aircraft around you are logged. The aircraft viewer lists them live, shows the route each one flew and saves captures.
- **LoRa mesh.** Fit the Cap LoRa-1262 and the Meshtastic and MeshCore nodes around you are logged. It only listens, and never sends.
- **GPS from anything.** A USB receiver, the Cap LoRa-1262, a Grove GPS unit or gpsd, found by itself.

**Logs and uploads**

- **The closest and the furthest sighting.** Each device is logged once at its strongest and once at its weakest sighting, with exact positions. No pile of repeats.
- **Logs WiGLE accepts.** WigleWifi-1.6 CSV, written only with a fresh GPS fix and a real clock, split into files every upload site takes.
- **Uploads by themselves.** Wi-Fi and Bluetooth go to WiGLE, WDGWars and Wardrift. Aircraft and mesh nodes go to WDGWars and Wardrift. Every log shows what each site did with it.
- **Keys from your phone.** Scan a QR code, enter the PIN, paste your keys. Or type them on the keyboard, and test each one from the device.

**And more**

- **Drive Alerts.** While you wardrive, a banner and a tone warn you about Flock cameras, Axon body cams, Meta glasses and any other kind you pick, from what the wardrive already hears. Each kind can have its own tone, such as a siren for body cameras. See [Drive Alerts](#drive-alerts).
- **Recon.** A separate mode that watches for ALPR and Flock cameras, body cameras, doorbell and security cameras, Meta glasses, trackers such as AirTag, Samsung SmartTag, Google Find Hub tags and Tile, Remote ID drones and more. It grades the evidence, warns you, tells you if a device seems to follow you, and has a signal finder to walk it down.
- **One job at a time.** Recon and a wardrive never run together, so every radio does one job well. Drive Alerts use no radio of their own.
- **The battery in view.** The level is at the top of every screen, with a notice at 20 % and at 10 %. At 5 % a running wardrive is saved in full and keeps recording. When the battery is almost empty, a running wardrive is saved and Quartermaster closes, so the Cardputer can switch off safely.

<table>
  <tr>
    <td><img src="docs/screens/hardware-mesh.png" width="270" alt="The Hardware screen with the RTL-SDR stick, the Cap LoRa-1262, a GPS and Bluetooth"></td>
    <td><img src="docs/screens/nodes.png" width="270" alt="Nodes: a new node found and one scanning"></td>
    <td><img src="docs/screens/recon.png" width="270" alt="Recon with four flagged devices"></td>
  </tr>
  <tr>
    <td align="center">Every radio on one screen</td>
    <td align="center">Nodes</td>
    <td align="center">Recon</td>
  </tr>
</table>

## Plug in and go

Quartermaster looks for your hardware by itself. There is nothing to set up for these:

- **Wi-Fi adapters.** The built-in radio and every [supported USB adapter](#wi-fi-adapters) scan. Plug one in or pull one out during a wardrive and it joins or leaves within seconds.
- **Bluetooth.** The Cardputer's own radio scans in every wardrive. If Bluetooth is not ready yet, Quartermaster tries again every few seconds.
- **GPS.** A USB GPS, the GPS on the Cap LoRa-1262, a Grove GPS unit or gpsd. Quartermaster tries each one and powers the cap and the Grove port only when it needs them. Plug a GPS in at any time, also during a wardrive.
- **Aircraft.** Plug in an RTL-SDR stick, before or during a wardrive, and Quartermaster listens for aircraft.
- **Mesh nodes.** Fit the Cap LoRa-1262 before you switch on, and every wardrive listens for Meshtastic and MeshCore nodes.

For aircraft and mesh nodes, restart the Cardputer once after the first install.

These still need a press:

- **Starting a wardrive.** Press **W**, then **OK** to stop it.
- **Nodes.** Flash the board, then add it with **OK** in **Settings > Nodes**. Wireless nodes are added there too. See [Nodes](#nodes).
- **Upload keys.** Enter them once. See [Upload keys](#upload-keys).
- **Country.** Set it once if you use nodes or the Cap LoRa-1262. With the cap, the first wardrive asks.

## Your first wardrive

1. Plug in your GPS, and any Wi-Fi adapters you have.
2. Open Quartermaster and, when the menu shows, press **H**. Every adapter is listed with its bands, and the GPS line turns green when it has a fix.
3. Press **Esc** to go back to the menu, then **W**. Until the GPS has a fix, the screen says **Waiting for GPS fix**. Once it has one, the three counters show Wi-Fi networks, Bluetooth devices and rows logged.
4. Press **OK**, and **OK** again to confirm. The wardrive stops, the log is saved and, if you have added keys, sent. To end a test drive and keep nothing, choose **Stop, don't save** instead and confirm once more.

### Waiting for a GPS fix

You can start a wardrive at any time, even before the GPS has a fix. Until the first fix that meets **GPS accuracy needed**, the wardrive waits:

- The radios start, so they are ready, but nothing is counted or logged and no log file is made. The aircraft and mesh logs wait too.
- The screen says **Waiting for GPS fix**, with the GPS it is using and how many satellites it sees. With no GPS found, it tells you what to plug in. The status bar shows **GPS WAIT** instead of the timer.
- Drive Alerts already work, as they are there to warn you. Alerts are not part of the wardrive's counts or its log.

At the first fix, the timer starts at 00:00:00, the counters start from zero and the log opens. Devices heard while waiting are logged as soon as they are heard again.

If the fix is lost later in the drive, logging pauses and the screen says so, but the timer keeps running and the counters keep what they have. Stopping before any fix saves nothing: the summary says **Nothing: GPS never had a fix**.

<p align="center">
  <img src="docs/screens/wardrive-waiting.png" width="320" alt="A wardrive waiting for its first GPS fix">
</p>

## Screens

<table>
  <tr>
    <td><img src="docs/screens/home.png" width="270" alt="Menu"></td>
    <td><img src="docs/screens/wardrive.png" width="270" alt="Wardrive"></td>
    <td><img src="docs/screens/summary.png" width="270" alt="Wardrive summary"></td>
  </tr>
  <tr>
    <td align="center">Menu</td>
    <td align="center">Wardrive</td>
    <td align="center">Summary</td>
  </tr>
  <tr>
    <td><img src="docs/screens/hardware.png" width="270" alt="Hardware"></td>
    <td><img src="docs/screens/logs.png" width="270" alt="Logs"></td>
    <td><img src="docs/screens/uploads.png" width="270" alt="Uploads"></td>
  </tr>
  <tr>
    <td align="center">Hardware</td>
    <td align="center">Logs, with a dot per upload site</td>
    <td align="center">Uploads</td>
  </tr>
  <tr>
    <td><img src="docs/screens/phone-setup.png" width="270" alt="Phone setup"></td>
    <td><img src="docs/screens/keys.png" width="270" alt="Keys"></td>
    <td><img src="docs/screens/settings.png" width="270" alt="Settings"></td>
  </tr>
  <tr>
    <td align="center">Phone setup</td>
    <td align="center">Keys</td>
    <td align="center">Settings</td>
  </tr>
  <tr>
    <td><img src="docs/screens/recon.png" width="270" alt="Recon"></td>
    <td><img src="docs/screens/recon-detail.png" width="270" alt="Recon device details"></td>
    <td><img src="docs/screens/recon-finder.png" width="270" alt="Recon signal finder"></td>
  </tr>
  <tr>
    <td align="center">Recon</td>
    <td align="center">Device details</td>
    <td align="center">Signal finder</td>
  </tr>
</table>

## Upload keys

Each site gives you a key on its own website. Quartermaster never asks for your password.

| Site | Where to find the key |
| --- | --- |
| [WiGLE](https://wigle.net) | Account page, then **Show My Token**. You need both the API Name and the API Token. |
| [WDGWars](https://wdgwars.pl) | Profile, then **Generate API key**. |
| [Wardrift](https://wardrift.net) | Dashboard, then **API Keys**. |

**From your phone or PC (easiest).** From the menu, press **K**, then **OK** on the first row. Scan the QR code, or open the address on the screen in a browser, and enter the PIN. Paste your keys and press Save. The page also lets you download logs and paste a list of networks to leave out. It only exists while that screen is open, on your own network, and it never shows a saved key in full, only its last four characters.

**On the Cardputer.** From the menu, press **K**, pick a key, press **OK** and type it. Press **T** to test it. A symbol is typed with **Sym** and the key it is printed on, or picked from the list that **Tab** opens.

Use any one site, or all three. Keys are stored on the device in a file only your user can read.

## Hardware

- **Wi-Fi adapters:** USB adapters with a driver in the CardputerZero's Linux kernel. See [Wi-Fi adapters](#wi-fi-adapters) for which ones.
- **GPS:** any USB receiver that sends NMEA (u-blox, Quectel LC29H and others), the Cap LoRa-1262, a Grove GPS unit, or gpsd.
- **Bluetooth:** the built-in radio.
- **Nodes:** ESP32-C5 boards running the Quartermaster Node firmware. See [Nodes](#nodes).
- **Aircraft:** an RTL-SDR stick (RTL2832U). See [Aircraft](#aircraft).
- **Mesh nodes:** the M5Stack Cap LoRa-1262 (SX1262). See [Mesh nodes](#mesh-nodes).

Recommended:

- **Wi-Fi:** the [AC1200 USB-C WiFi Card](https://hackergadgets.com/products/ac1200-usb-c-wifi-card) from HackerGadgets, a tri-band MT7921AU adapter for 2.4, 5 and 6 GHz with a USB-C connector.
- **Other Wi-Fi adapters that need nothing set up:** any MT7921AU adapter, the TP-Link Archer T2U or T3U, the TP-Link Archer TX10UB Nano, and RTL8811CU nano adapters.
- **GPS:** the Dual Band GNSS USB Dongle from [AWOK Dynamics](https://awokdynamics.com).

Tested on a CardputerZero with an ALFA AWUS036AXM and the HackerGadgets AC1200 scanning together, and an LC29H USB GPS.

### Wi-Fi adapters

Not every USB Wi-Fi adapter works. An adapter works when the CardputerZero's Linux (6.18) has a driver for its chip. Quartermaster only scans, so it needs no monitor mode, and every adapter marked Yes scans with nothing to set up. The chip matters, not the brand: look up the chip of your model before you buy.

| Chip | Example adapters | Works? |
|---|---|---|
| MediaTek MT7921AU | ALFA AWUS036AXM / AXML, Netgear A8000, Panda PAU0F, COMFAST CF-952AX | Yes, 2.4, 5 and 6 GHz |
| MediaTek MT7925U | Netgear A9000 | Yes |
| MediaTek MT7612U, MT7610U, MT7601U | ALFA AWUS036ACM, Netgear A6210, TP-Link Archer T2UH, ALFA AWUS036ACHM | Yes, with firmware-misc-nonfree |
| Realtek RTL8812AU, RTL8811AU / 8821AU | ALFA AWUS036ACH, TP-Link Archer T2U / T2U Nano / T4U, Netgear A6100 | Yes |
| Realtek RTL8814AU | ALFA AWUS1900, Netgear A7000, ASUS USB-AC68 | Yes, with enough power |
| Realtek RTL8812BU / 8822BU, RTL8822CU | TP-Link Archer T3U / T3U Plus, Netgear A6150 | Yes |
| Realtek RTL8811CU / 8821CU, RTL8723DU | Most "AC600" and "AC650" nano adapters | Yes |
| Realtek RTL8851BU, RTL8852BU | TP-Link Archer TX10UB Nano, TX20U | Yes, but some newer models are not known to Linux 6.18 yet |
| Realtek RTL8192EU, RTL8188EU, RTL8192CU and other N150 / N300 | TP-Link TL-WN722N v2/v3, TL-WN822N, Edimax EW-7811Un | Yes, 2.4 GHz |
| Atheros AR9271 | ALFA AWUS036NHA | Yes, 2.4 GHz |
| Ralink RT3070 / RT5370 / RT5572 | ALFA AWUS036NH, Panda PAU09 | Yes, with firmware-misc-nonfree |
| Realtek RTL8832AU / 8852AU | ASUS USB-AX56, D-Link DWA-X1850 A1, ALFA AWUS036AXER | Not yet. Needs Linux 6.19 |
| Realtek RTL8832CU / 8852CU | TP-Link Archer TX50U | Not yet. Needs Linux 6.19, and the TX50U Linux 7.0 |
| Realtek RTL8912AU / 8922AU (Wi-Fi 7) | ASUS and Edimax Wi-Fi 7 adapters | Not yet. Needs Linux 7.2 |

The "Not yet" chips need a newer Linux on the CardputerZero, which an app cannot install. They start working when the CardputerZero image moves to a newer kernel.

Small and nano adapters are the safest choice. Large adapters with several antennas, such as the ALFA AWUS036AXML, AWUS036ACH and AWUS1900, can draw more than the Cardputer's USB port gives. One ALFA AWUS036AXML did not start on the test device.

MT7601U, MT7610U, MT7612U and Ralink adapters need firmware that the CardputerZero image leaves out. Install it once with `sudo apt install firmware-misc-nonfree`. Quartermaster does not install it for you, so that its own install never depends on a package the Cardputer may not be able to fetch.

Many cheap Realtek adapters first show up as a small disc with a Windows driver on it. Quartermaster installs usb-modeswitch, and most of them switch to Wi-Fi by themselves after a few seconds. Some do not yet. If one stays in setup mode, the stock usb-modeswitch data does not know it yet. For the RTL8192FU disc (`0bda:a192`), run `sudo usb_modeswitch -v 0bda -p a192 -K`.

When an adapter is plugged in but does not scan, the Hardware screen says why:

- **No driver for it here. It needs Linux 6.19** (or another version). The chip needs a newer Linux than the CardputerZero has. See the table.
- **This system has no driver for it.** The chip has no driver in this Linux.
- **This model is too new for the driver here.** The chip is supported, but this model came out after Linux 6.18.
- **Firmware missing.** Install the package it names, for example `sudo apt install firmware-misc-nonfree`. When it says the system needs newer firmware, the package is there but too old for this adapter.
- **Needs more power.** Use a powered USB hub or a smaller adapter.
- **Wi-Fi adapter in setup mode.** The adapter still shows as a driver disc. Replug it.
- **On USB but not starting.** Replug it or use a powered hub.

### USB power and Power guard

The Cardputer runs on its battery, and its one USB port can only give so much power. Every USB Wi-Fi adapter tells Linux it needs 100 mA, so Linux never warns you. A tri-band adapter such as the ALFA AWUS036AXM really takes about 600 mA, and up to 900 mA when it starts. When the adapters, an RTL-SDR stick and a node on an unpowered hub ask for more than the port gives, devices drop off the bus, the hub can drop off with everything on it, and the GPS can go with it.

The Hardware screen adds up what is plugged in, from a table of typical draw for each chip. The numbers are estimates:

| Device | Typical | Peak |
|---|---|---|
| MT7921AU (ALFA AWUS036AXM and other tri-band adapters) | 600 mA | 900 mA |
| RTL8812AU, RTL8812BU (AC1200 adapters) | 500 mA | 700 mA |
| MT7612U | 450 mA | 650 mA |
| RTL-SDR stick | 300 mA | 300 mA |
| ESP32-C5 node | 150 mA | 350 mA |
| USB GPS (CP210x) | 50 mA | 50 mA |

It counts the Cardputer's port, about 1,000 mA, and takes about 900 mA as the most each unpowered hub on it can pass on (the hub itself counts as about 50 mA). Devices on a hub with its own power supply are not counted. Some powered hubs say they have no power of their own: set **USB hub power** to **Hub has power** in Settings > Scanning for those. When the total is over, the Hardware screen says "Too many adapters for USB power" and to use a powered USB hub. When a node sits on a crowded unpowered hub, it suggests running the node on its own battery: nodes work wirelessly too.

Power guard is on by default, under Settings > Scanning. During a wardrive it:

- Starts the USB adapters, the RTL-SDR stick and the nodes about 1.5 seconds apart, so their start-up spikes do not add up. An adapter plugged in during the drive waits 1.5 seconds too. With only one of them plugged in, nothing waits.
- Watches for signs that power runs short: a USB device starting again on the same port, an adapter that goes away and comes back, a hub dropping off with several devices at once, the Pi's under-voltage alarm, power errors in the kernel log when the app may read it, and sharp dips in battery voltage. It needs two of these signs from separate checks within 60 seconds, or one strong sign: the under-voltage alarm, or a kernel line about under-voltage or over-current. A device or a hub unplugged or replugged by hand is not enough, and the errors a driver prints while its adapter is unplugged are left out.
- Only while it sees those signs, eases the extra adapters, one step at a time: first it undoes Radio tuning on them, then it spaces out their sweeps, then it pauses the adapter whose bands the others cover best. An adapter plugged in later gets the gentle steps first too. The adapter that keeps 6 GHz is never paused while another can be. The GPS, the built-in radio and the nodes are never touched.
- After a few minutes of steady power, it tries a paused adapter once more. If trouble comes back, that adapter stays paused while it is plugged in. Unplug it, or move it to a powered hub, and it scans again.
- Also counts the USB port running out of channels (dwc2 "no free channel"; the harmless "ChHltd" lines and lines from an adapter just unplugged are left out) and USB errors (-71) on adapters, when the app may read the kernel log, as power trouble, and the Hardware screen shows "USB port at its limit".
- When an adapter that worked earlier in the drive stays on USB but has no network interface any more, as after a brownout, the radio helper resets its USB port, at most 3 times and further apart each time (10, 30 and 90 seconds). Then the Hardware screen says it needs power. A reset needs the radio helper running, which it is only when Deep scan, Direct scans or Radio tuning is on. Only the radio helper may reset, and only the known adapters listed in the install's udev rule: the rule gives their USB device nodes to a `quartermaster-usbreset` group that only the helper is in, and runs no program.
- Tells you once on the Wardrive screen. The Hardware screen shows what it eased and why. When it could not ease anything, for example with only one USB adapter, it tells you to use a powered USB hub.

During a wardrive the Hardware screen sums it up in one line, such as "Adapters: 4 active, est. 2.1 A, hub: unpowered" (or "hub: none" with no hub). In that line a listening adapter counts at about 60% of its typical draw, as it never transmits. The USB power estimate and Power guard count every adapter at its full draw.

When power is fine, Power guard changes nothing: every adapter scans and is tuned as before and the logs are the same. It never changes the country, never fakes power limits and never adds a node. A paused adapter still draws a little power while plugged in, so for many adapters the real fix is a powered USB hub.

## Nodes

A Quartermaster Node is a small ESP32-C5 board on USB that scans Wi-Fi on 2.4 and 5 GHz, and Bluetooth when you ask, beside your adapters. Quartermaster shares the channels out between the nodes, so with more nodes each channel is heard more often. Up to 8 nodes can be used at once.

These boards are supported:

- **Seeed XIAO ESP32-C5**
- **Espressif ESP32-C5-DevKitC-1**, with a chip of revision v1.0 or later. Early boards carry a v0.1 sample chip, which cannot run the node firmware.
- **M5Stack Stamp-C5**

Other ESP32-C5 boards with 4 MB of flash or more may work too.

1. Plug the board in. If it already runs the node firmware, Quartermaster says **New node found**. If not, flash it first (see below).
2. Open **Settings > Nodes** and press **OK** on the new node to add it. It scans in every wardrive and in Recon from then on.
3. Press **OK** on a node to rename it, turn its Bluetooth on (that halves its time for Wi-Fi), tell it which light the board has, blink that light, update its firmware or remove it. A node uses no light until you choose one.

Set **Settings > Country** to where you are: the nodes only use the Wi-Fi channels allowed there, and the Cap LoRa-1262 the LoRa band. Quartermaster asks the first time you open **Settings > Nodes**, or start a wardrive with the cap fitted. Until then it uses United States. The Hardware screen has a card for each node with the channels it scans.

If gpsd is installed, plug a node in by its own USB port, not a UART port. gpsd may take a UART port for a GPS.

### Wireless nodes

Nodes do not have to be plugged in. One node stays on the CardputerZero as the **hub**, and up to 8 more run on a battery or a USB charger and send what they hear to the hub by radio. Each wireless node scans its own share of the channels where it is, within radio range of the hub.

1. Open the node that stays plugged in and turn on **Hub for wireless nodes**. The hub does not scan. There is one hub: turning it on for one node turns it off for the others. It needs node firmware 1.1.0 or later.
2. In **Settings > Nodes**, choose **Add a wireless node**. Pairing stays open for 60 seconds. Press **OK** to open it again.
3. Power the new node from a battery or a USB charger, not from the Cardputer, and hold its **BOOT** button for 2 seconds (on a Stamp-C5, hold G28 to GND). It shows up as **New wireless node**. Press **OK** to add it.

Wireless nodes start and stop with every wardrive and Recon. A wireless node's screen and the Hardware screen say when it was last heard and how well the hub hears it. A node that has not been heard for 30 seconds hands its channels to the others and gets them back when it is heard again. When the hub is unplugged, the wireless nodes wait for it and the nodes on USB scan every channel. Removing a wireless node makes the hub forget it; hold BOOT on it to add it again. A wireless node plugged in by USB scans as a USB node until it is unplugged. Moving the hub to another node means adding every wireless node again: Quartermaster asks first, and each one says so until it is added.

A wireless node is somewhere within radio range of the hub, not at the Cardputer. So its rows get the Cardputer's position from when the node heard the device, with an accuracy 50 m wider. They are never used as a device's furthest row.

### Flashing a node

**From the Cardputer.** Plug the board in and open **Settings > Flash a node**. Pick the board. A board without the node firmware gets **Install**, which erases it. A node you already have can choose **Update**, which keeps its settings, or **First install**. A progress bar shows how far it is. A node whose firmware is older than the one that came with Quartermaster also shows **Update firmware** on its screen in **Settings > Nodes**.

**From a computer or a phone.** The [flasher web page](https://valleytechsolutions.github.io/Quartermaster/flasher/) works in Chrome or Edge on a computer, or Chrome on Android. Plug the board in, press **First install** for a new board, or **Update, keep settings** for a board that already runs Quartermaster Node, and pick the board in the list. Its files are in [docs/flasher](docs/flasher/). Safari and the browsers on iPhone and iPad cannot flash a board.

Flashing from the Cardputer waits until no wardrive or Recon runs. If a board does not answer, put it in download mode and try again:

- **Seeed XIAO ESP32-C5:** hold BOOT while you plug it in.
- **M5Stack Stamp-C5:** hold G28 to GND while you plug it in.
- **ESP32-C5-DevKitC-1:** hold BOOT and press RESET.

<table>
  <tr>
    <td><img src="docs/screens/nodes.png" width="270" alt="Nodes"></td>
    <td><img src="docs/screens/node-detail.png" width="270" alt="A node"></td>
    <td><img src="docs/screens/flashing.png" width="270" alt="Flashing a node"></td>
  </tr>
  <tr>
    <td align="center">Nodes</td>
    <td align="center">A node</td>
    <td align="center">Flashing a node</td>
  </tr>
  <tr>
    <td><img src="docs/screens/pairing.png" width="270" alt="Adding a wireless node"></td>
  </tr>
  <tr>
    <td align="center">Adding a wireless node</td>
  </tr>
</table>

The firmware, the boards it runs on and the serial protocol are described in [firmware/README.md](firmware/README.md).

## Aircraft

With an RTL-SDR stick (RTL2832U) on USB, Quartermaster also logs the aircraft around you, for WDGWars and Wardrift. WiGLE does not take aircraft, so they are never sent there.

1. Nothing to install: the radio tools, rtl-sdr, come with Quartermaster. They add no service, so nothing runs until a wardrive listens or you open the aircraft viewer.
2. Restart the Cardputer once after the first install. This keeps the TV driver off the stick and gives you access to it.
3. Plug in the stick. **Settings > Aircraft (ADS-B)** is on **Auto**, so Quartermaster listens whenever the stick is plugged in, also when you plug it in during a wardrive. **On** always listens and says when the stick is missing. **Off** never listens.
4. Start a wardrive. The Wardrive screen counts the aircraft beside the radios, and the Hardware screen shows the stick.

During a wardrive Quartermaster starts `rtl_adsb` at a low priority on one CPU, with no network ports, and stops it with the wardrive. It reads what the stick hears and works out each aircraft itself. Wi-Fi and Bluetooth come first: when the air is very busy, some aircraft messages are skipped rather than slow the wardrive.

If readsb or dump1090 already runs on the Cardputer, Quartermaster reads what it writes to `/run/readsb`, `/run/adsb`, `/run/dump1090-fa` or `/run/dump1090-mutability` and does not start anything. Without the stick the wardrive goes on as ever, and the Hardware screen says why. If it says there is no permission, or asks you to restart, restart the Cardputer once.

Each aircraft is kept once, with its closest and its furthest position. When the wardrive stops, `quartermaster_<date>-<time>.aircraft.json.gz` (readsb's own aircraft.json format, at the closest position) is written beside the WiGLE log and goes to WDGWars and Wardrift. Wardrift only gives points for aircraft heard in the last 3 days, so keep uploads on.

### Aircraft viewer

On the Hardware screen, press **A** while the RTL-SDR card says it is ready or running. The viewer lists every aircraft the stick hears, with or without a wardrive.

- Each row: callsign (or the ICAO code), height, speed, distance and direction from you, and when it was last heard. The nearest is first. **Tab** puts the latest first.
- **OK** shows all an aircraft sent. What it did not send says "Unknown". **OK** again shows its route: the positions heard, on a map with north up, you as the cross and two range rings.
- **S** starts a capture and **S** again ends it. Leaving the viewer ends it too. **L** lists the captures.
- Origin and destination are not sent by aircraft, so the route is the track heard, not the flight plan.
- During a wardrive the viewer reads the frames the wardrive already hears. It starts nothing and takes nothing from the wardrive.

A capture is two files in `quartermaster/aircraft`: `.track.csv` with one line per position (time in UTC, ICAO, callsign, latitude, longitude, height in feet, speed in knots, heading, vertical rate in feet per minute, squawk), and `.frames.avr` with every raw Mode S frame as an AVR line: `@`, a 12 digit timestamp, the frame and `;`. The timestamp counts in steps of 1/12,000,000 second from the second in the file name. dump1090 and readsb read this on their raw input port (30001). Times come from the GPS. A capture begun before the GPS told the time is moved to that time once it does.

<table>
  <tr>
    <td><img src="docs/screens/wardrive-aircraft.png" width="270" alt="Wardrive with aircraft"></td>
    <td><img src="docs/screens/hardware-aircraft.png" width="270" alt="The RTL-SDR stick on the Hardware screen"></td>
    <td><img src="docs/screens/summary-aircraft.png" width="270" alt="Wardrive summary with aircraft"></td>
  </tr>
  <tr>
    <td align="center">Wardrive with aircraft</td>
    <td align="center">The stick on the Hardware screen</td>
    <td align="center">Summary</td>
  </tr>
  <tr>
    <td><img src="docs/screens/aircraft.png" width="270" alt="The aircraft viewer: six aircraft with height, speed, distance and age"></td>
    <td><img src="docs/screens/aircraft-detail.png" width="270" alt="One aircraft: ICAO code, callsign, squawk, category, height, speed, heading, vertical rate and position"></td>
    <td><img src="docs/screens/aircraft-route.png" width="270" alt="The route of one aircraft on a map with two range rings, and its positions"></td>
  </tr>
  <tr>
    <td align="center">Aircraft viewer</td>
    <td align="center">One aircraft</td>
    <td align="center">Its route</td>
  </tr>
</table>

## Mesh nodes

With the M5Stack Cap LoRa-1262 fitted, Quartermaster also logs the Meshtastic and MeshCore nodes around you, for WDGWars and Wardrift. WiGLE does not take mesh nodes, so they are never sent there. Quartermaster only listens. It never sends anything on LoRa.

1. Nothing to install: what the cap needs comes with Quartermaster.
2. Restart the Cardputer once after the first install. This gives Quartermaster access to the cap.
3. Fit the cap before you switch on. Quartermaster checks once, when it opens, that the radio on the cap answers. Without the cap nothing changes and nothing is slower.
4. **Settings > LoRa mesh** is on **Auto**, so every wardrive listens when the cap was found. **On** always listens and says when the cap is missing. **Off** never listens.
5. Set **Settings > Country** to where you are. Quartermaster asks the first time you start a wardrive with the cap. It picks the LoRa band:

| Country | Meshtastic (LongFast, default channel) | MeshCore (public) |
| --- | --- | --- |
| United States, Canada, Mexico | 906.875 MHz | 910.525 MHz |
| Europe and the United Kingdom | 869.525 MHz | 869.618 MHz |
| Australia | 919.875 MHz | 915.800 MHz |
| New Zealand | 919.875 MHz | 917.375 MHz |
| South Korea | 922.875 MHz | |
| Brazil | 903.875 MHz | |

The cap does not reach the bands of other countries, so there it does not listen and the Hardware screen says so. Meshtastic and MeshCore use different settings, so one radio cannot hear both at the same time. Where there are two, Quartermaster listens to each in turn, 30 seconds at a time.

The radio has a thread of its own and is never read by the screen, so Wi-Fi and Bluetooth are not slowed. The cap's GPS still works while it listens.

What is kept:

- **Meshtastic.** Every node heard on the default channel, by its node number. Its name, role and the position it gives out are read only from the public default channels. Packets that came over the internet (MQTT) are skipped.
- **MeshCore.** Nodes that send an advert. An advert only counts when its signature is right.
- Each node is kept once, with its closest and its furthest sighting at the Cardputer's position, the signal (RSSI and SNR) and how many hops away it was. A node closer in hops always counts as closer. The position the node gives out about itself is kept beside it.

Every 30 seconds the nodes are saved to a hidden file next to the log. When the wardrive stops, `quartermaster_<date>-<time>.mesh.json.gz` is written beside the WiGLE log, and goes to WDGWars and Wardrift:

- **WDGWars** gets each node at the position it gives out about itself, as the WDGWars mesh uploaders do. Nodes that give no position are not sent. A file with no such node says so in Logs.
- **Wardrift** gets every node, with its position when it gives one.

Logs shows mesh files with a **MESH** tag, and what each site took.

<table>
  <tr>
    <td><img src="docs/screens/wardrive-mesh.png" width="270" alt="Wardrive with mesh nodes"></td>
    <td><img src="docs/screens/hardware-mesh.png" width="270" alt="The Cap LoRa-1262 on the Hardware screen"></td>
    <td><img src="docs/screens/summary-mesh.png" width="270" alt="Wardrive summary with mesh nodes"></td>
  </tr>
  <tr>
    <td align="center">Wardrive with mesh nodes</td>
    <td align="center">The cap on the Hardware screen</td>
    <td align="center">Summary</td>
  </tr>
  <tr>
    <td><img src="docs/screens/logs-mesh.png" width="270" alt="A mesh file in Logs"></td>
    <td><img src="docs/screens/log-detail-mesh.png" width="270" alt="A mesh file and where it went"></td>
    <td></td>
  </tr>
  <tr>
    <td align="center">A mesh file in Logs</td>
    <td align="center">Where it went</td>
    <td></td>
  </tr>
</table>

### LoRaWAN networks

Turn on **LoRaWAN networks** in **Settings > Alert me about** and, while you wardrive with the cap, Quartermaster also listens for LoRaWAN uplinks, as SquachWatch does: a third turn of 30 seconds after Meshtastic and MeshCore, on one uplink channel at SF7 (903.9 MHz in the US, 868.1 MHz in Europe, 916.8 MHz in Australia, New Zealand and Brazil, 922.1 MHz in South Korea). It is off by default, and with it off the cap listens exactly as before.

Each LoRaWAN frame carries its device address in the clear, and the start of that address names the network operator, such as The Things Network, Helium or a utility. Quartermaster looks it up in the LoRa Alliance NetID list that SquachWatch carries and alerts once per operator, not once per device, under an address made up from the NetID (02:4C:57:...), and its alert screen shows the NetID. The payload stays encrypted and is never read. LoRaWAN devices are not mesh nodes, so they are not written to the mesh file and are not uploaded.

What is not there yet: Quartermaster listens on one uplink channel and one data rate only, so a network that uses other channels or slower rates may not be heard. It does not listen to downlinks (RX2) or Class B beacons, which need inverted IQ, and it does not read join requests.

## Controls

| Key | What it does |
| --- | --- |
| **F X Z C** or the arrows | Up, down, left, right |
| **OK** (Enter) | Open or confirm |
| **Esc** | Back |
| **Space** | Start or stop a wardrive from the menu |
| **W R L U H S K A** | From the menu: jump to Wardrive, Recon, Logs, Uploads, Hardware, Settings, Keys, About |
| **Tab** | On the wardrive screen: show Wi-Fi, Bluetooth or both. In a text box: list every symbol |
| **A** | On the wardrive screen: the alerts of this wardrive |
| **N** | On an alert's details (OK on an alert in that list, then N): no more alerts for that device |
| **Del** | While an alert banner shows: hide it |
| **Q** | On the wardrive screen, the alert list or an alert banner: no alert tones or banners for 10 minutes. Again to end it |
| **Sym**, then a key | In a text box: the symbol printed on that key |

Every screen shows its own keys along the bottom.

## Settings

| Section | Setting | Choices | Default |
| --- | --- | --- | --- |
| Scanning | Wi-Fi adapters | All, USB only, Built-in only | All |
| | Wi-Fi bands kept | All, 2.4 GHz, 5 GHz, 6 GHz, 5 + 6 GHz | All |
| | Pause between sweeps | None, 1 s, 3 s, 10 s | None |
| | Power guard | On, Off | On |
| | USB hub power | Auto, Hub has power | Auto |
| | Weakest signal kept | Everything, -90, -85, -80, -70 dBm | Everything |
| | Bluetooth scan | On, Off | On |
| | Random BLE addresses | Log, Skip | Log |
| | Bluetooth check | 0.5 s, 1 s, 2 s, 5 s | 1 s |
| | Aircraft (ADS-B) | Auto, On, Off | Auto |
| | LoRa mesh | Auto, On, Off | Auto |
| | Country | 44 countries, or Anywhere else | United States |
| Deep scan | Deep scan | Off, On | Off |
| | Listen pattern | Balanced, Flock first | Balanced |
| | All adapters listen | Auto, Off | Auto |
| | Monitor adapter | Auto, or one USB adapter | Auto |
| | Direct scans | Off, On | Off |
| | Radio tuning | Off, During a wardrive | Off |
| Nodes | Nodes | The nodes in use, and new ones to add | |
| | Flash a node | Install or update the node firmware | |
| Logging | Rows per device | Closest + furthest, Full track | Closest + furthest |
| | Log when signal changes (Full track) | 2, 4, 6, 10 dB | 4 dB |
| | Log again after moving (Full track) | 10, 25, 50, 100 m | 25 m |
| | Shortest time between rows (Full track) | 5, 10, 30, 60 s | 30 s |
| | Hidden networks | Log, Skip | Log |
| | Ignored networks | Names or addresses that are never logged | |
| GPS | GPS source | Auto, gpsd, USB, Cap / Grove, Off | Auto |
| | GPS accuracy needed | Any fix, Fair, Good, Best | Any fix |
| | Cap and Grove power | When needed, Leave off | When needed |
| Uploads | Send logs | Automatic, Only when asked | Automatic |
| Drive alerts | Alerts while wardriving | On, Off | On |
| | Alert me about | Each kind of device: alert on or off, tone on or off, and its tone | Flock cameras, Axon body cams, Meta glasses, My alerts |
| | Alert tones | On, Off | On |
| | Tone volume | Medium, Loud, Quiet. Also the Recon beep | Medium |
| | Alert position | Bottom, Top. Also Recon alerts | Bottom |
| | Alert from | Weak hints, Medium evidence, Strong only | Medium evidence |
| | Alert again (moving devices) | After 15 min, After 1 h, Once per drive. Fixed cameras alert once per drive | After 15 min |
| | Only when signal is above | Any, -85 dBm, -75 dBm | Any |
| | My alerts | Up to 8 names, patterns or addresses that always alert | |
| | No alerts for | Up to 32 devices silenced from an alert with **N**. Still logged | |
| Recon | Show devices from | Weak hints, Medium evidence, Strong only | Weak hints |
| | Alert from | Weak hints, Medium evidence, Strong only | Medium evidence |
| | Alert sound | On, Off | On |
| | Watch list | The kinds of device Recon looks for. Deauth floods, evil twin APs, LoRaWAN networks and My alerts are Wardrive only | All but proximity beacons and smart speakers |
| Screen | Live list shows | Wi-Fi + BLE, Wi-Fi, Bluetooth | Wi-Fi + BLE |
| | Start wardrive on launch | No, Yes | No |
| | Boot animation | On, Off | On |
| About | Check for updates | Asks the AppStore and GitHub | |

## Logs

Everything is kept in the `quartermaster` folder in your home directory.

| File | What it is |
| --- | --- |
| `logs/quartermaster_<date>-<time>.wigle.csv` | The wardrive in WigleWifi-1.6 format, ready for WiGLE, WDGWars and Wardrift. |
| `logs/quartermaster_<date>-<time>.summary.csv` | One line per device: name, security, channel, first and last seen, and the strongest and weakest sighting with position and time. |
| `logs/quartermaster_<date>-<time>.aircraft.json.gz` | The aircraft of the wardrive, each at its closest position, for WDGWars and Wardrift. |
| `logs/quartermaster_<date>-<time>.aircraft.csv` | One line per aircraft: callsign, squawk, first and last seen, signal, and the closest and furthest position with height, speed and time. |
| `logs/quartermaster_<date>-<time>.mesh.json.gz` | The Meshtastic and MeshCore nodes of the wardrive, each with its closest and furthest sighting, for WDGWars and Wardrift. |
| `logs/quartermaster_<date>-<time>.mesh.csv` | One line per mesh node: name, role, first and last seen, signal, hops, the closest and furthest sighting with position and time, and the position the node gives out. |
| `aircraft/quartermaster_<date>-<time>.track.csv` | A capture from the aircraft viewer: one line per position heard. Never uploaded. |
| `aircraft/quartermaster_<date>-<time>.frames.avr` | The raw Mode S frames of that capture, as AVR lines with timestamps. Never uploaded. |
| `recon/recon_<date>-<time>.jsonl` | What Recon flagged. |
| `recon/drive_<date>-<time>.jsonl` | What Drive Alerts found during a wardrive, alerted or not. Never uploaded. |

Every log starts with a header that names the app:

```
WigleWifi-1.6,appRelease=Quartermaster 1.2.9,model=CardputerZero,release=...,device=Quartermaster,display=320x170,board=M5Stack CardputerZero,brand=Valleytech Solutions,star=Sol,body=3,subBody=0
```

By default each device has at most two rows in a log: its strongest sighting, the closest pass, and its weakest, the far edge of its range, each with its own position and time. A weak sighting below -92 dBm, or with a position less sure than 25 m, is not used as the far edge. One row is written when there is nothing better. Until the wardrive stops, the rows wait in a hidden file next to the log, so the log appears in Logs and goes to the upload sites once the wardrive stops. After a crash or a power cut, Quartermaster finishes that log the next time it opens.

With **Rows per device** set to **Full track**, a device also gets a new row when its signal is stronger or weaker than anything logged so far, or when you have moved and enough time has passed. Its exact strongest and weakest sightings are always in the file by the time the wardrive ends.

A log is split at 8 MB so that every site accepts it.

To copy logs off the Cardputer, open the phone setup page and tap a log, or use SFTP.

## Recon

Recon listens on every radio for devices that look like surveillance or tracking equipment. Each match is graded as a weak hint, medium evidence or a strong signature, and the details screen shows exactly what was matched. Press **OK** on a device and **OK** again to open the signal finder, then walk toward the stronger signal.

On other screens, a Recon alert shows as a card at the bottom, where Drive Alerts show theirs, or at the top with **Alert position**. **Del** hides it. A device that stays nearby alerts once, and again only after it has been gone for 15 minutes, or when it starts to follow you.

A match is broadcast evidence. It is not proof that a device is recording, who owns it, or what it is for. Many products share radio parts, so weak hints are common.

Recon also knows dashcams and fleet cameras, in-car fleet routers, smart speakers with microphones, AirTags with or without their owner and AirTags not set up yet, and the Dragon Detector fox test beacon.

The kinds of device are SquachWatch's detection types, with its names and in its order, in Recon and in Drive Alerts alike:

| SquachWatch type | Shown as | What it covers |
| --- | --- | --- |
| FLOCK | Flock cameras | Flock cameras, batteries and accessories |
| AXON | Axon body cams | Axon, WatchGuard and Digital Ally body cameras. Cradlepoint and Sierra Wireless in-car routers go here too, as SquachWatch files them, as weak hints named "fleet router", so they never alert unless **Alert from** is set to weak hints |
| META | Meta glasses | Ray-Ban Meta, Oakley Meta, Snap Spectacles and other camera glasses |
| SKIMMER | Card skimmers | Serial Bluetooth modules found in card skimmers |
| RAVEN | Raven sensors | Raven gunshot sensors |
| AIRTAG | AirTags | AirTags and other Find My accessories |
| DRONE | Drones | Remote ID broadcasts and drone makers |
| ALPR | ALPR cameras | Plate readers other than Flock |
| CAMERA | Cameras | Security cameras, dashcams and hidden camera hotspots |
| SAMSUNG_TAG | Samsung tags | Samsung SmartTag |
| GOOGLE_TAG | Google tags | Find Hub trackers such as Chipolo, Pebblebee and Moto Tag, and unknown-tracker (DULT) adverts |
| TILE | Tile trackers | Tile |
| RING | Ring cameras | Ring doorbells and cameras |
| DEAUTH | Deauth floods | Bursts of Wi-Fi disconnects, from a Dragon Detector |
| EVILTWIN | Evil twin APs | One network name open on one access point and protected on another |
| IBEACON | Proximity beacons | iBeacons. Off in Recon until you turn it on |
| HACKER | Hacker hardware | Flipper Zero, Pwnagotchi, WiFi Pineapple, deauthers and the like, and bursts of fake Bluetooth adverts. The Dragon Detector fox test beacon is listed here as a weak hint and never alerts unless **Alert from** is set to weak hints |
| LORAWAN | LoRaWAN networks | The operator of a LoRaWAN network in range, with the Cap LoRa-1262. See [LoRaWAN networks](#lorawan-networks) |
| MINE | My alerts | Your own names and addresses |
| LISTENER | Smart speakers | Sonos, HomePod, and Echo or Google speakers in setup. Off in Recon until you turn it on |

Recon hears Wi-Fi and Bluetooth only, so its watch list leaves out deauth floods, evil twins, LoRaWAN networks and My alerts.

## Drive Alerts

While you wardrive, Quartermaster can warn you about the devices you pick, from what the wardrive already hears. It never scans more, never slows a sweep, and never changes a WiGLE log: the logs are the same with alerts on or off. Each sighting gets a quick look-up, and only the few that may matter go to a low-priority thread that grades them with Recon's classifier.

<p align="center">
  <img src="docs/screens/wardrive-alert.png" width="320" alt="A Flock alert banner over the wardrive screen">
  <img src="docs/screens/alert-types.png" width="320" alt="Alert me about: each kind with its tone and switch">
</p>

- **What alerts.** Out of the box: Flock cameras, Axon body cams, Meta glasses and My alerts. **Settings > Alert me about** turns every other kind on or off, from ALPR and Ring cameras to AirTags, Samsung, Google and Tile trackers, evil twin APs, hacker hardware and LoRaWAN networks.
- **Tones.** Siren for Axon body cams, Chirp for Flock and Ping for Meta glasses, Blip for the rest. Every kind can have its own: Siren, Yelp, Chirp, Shutter, Ping, Blip or Silent. In **Alert me about**, **Z** and **C** pick a tone and play it, **P** plays it again, **T** turns the tone of one kind off and **D** restores the defaults. **Alert tones** turns every tone off.
- **The banner.** A card at the bottom, just above the key hints, for 6 seconds, 10 for a body camera. **Alert position** can put it at the top instead; on the wardrive screen it then sits just under the counters. It never covers the counters, and **OK** still stops the wardrive. **Del** hides it at once; the alert stays in the list and the next new alert still shows. The icon pulses once a second for the first 3 seconds, then stays still. More of the same kind add to the card (×3) instead of replacing it. A more urgent kind takes its place at once; others wait their turn, and several waiting together show as one card, such as "3 new alerts". The wardrive screen counts the alerts, and **A** lists them.
- **Not too many.** A device alerts once while it is around. Flock and other fixed cameras alert once per drive, however often you pass them; body cameras and other moving devices alert again after they have been gone for 15 minutes (**Alert again (moving devices)**). The log still notes every pass. One camera heard on Wi-Fi and Bluetooth, a Bluetooth device that changes its address, and a Dragon Detector line about a camera the scan just heard are each one alert. Weak hints are saved but do not alert. Most kinds need two sightings first; cameras, Meta glasses, drones, hacker hardware, LoRaWAN networks and My alerts alert at once. A LoRaWAN network alerts once per drive.
- **Quiet for 10 minutes.** **Q** on the wardrive screen, on the alert list or while a banner shows: no tones and no banners for 10 minutes. Alerts are still listed and logged, and the wardrive screen shows how long is left, such as Q 9m, in place of the alert count. **Q** again ends it.
- **Calm tones.** No tones in the first minute of a wardrive; those alerts still show and are not sounded later. A Flock camera tone plays at most once a minute, a My alert every 30 seconds, Meta glasses every 2 minutes, and body cameras and other kinds every 5 minutes. More body cameras in between add to the banner without a sound. In a busy town a kind sounds less often: after 5 tones in 10 minutes its gap doubles, up to 4 minutes. Tones are spaced 8 seconds apart, at most 30 an hour. A body camera or My alert sounds even then, up to 6 times an hour each. A flood of new devices of one kind, such as a Flipper sending fake adverts, mutes that kind for 5 minutes; Flock cameras, body cameras, Meta glasses and My alerts are never muted, and a flood of them shows one card such as "30 flock cameras in 1 min". **Tone volume** sets the volume of Drive Alerts and of the Recon beep.
- **No alerts for this.** Open an alert from the list and press **N**: that device does not alert again, but it is still logged and uploaded, unlike an ignored network. Its address is kept, or its name when its Bluetooth address keeps changing; then anything with that name stops alerting. A device whose Bluetooth address keeps changing and that has no name, such as most Meta glasses, is silenced for this drive only. **Settings > No alerts for** lists the kept ones.
- **My alerts.** Up to 8 Wi-Fi or Bluetooth names, patterns or addresses that always alert, whatever kind of device: `veh` catches every name that starts with veh, `*` and `?` match any text, and capitals do not matter.
- **Dragon Detector.** Plug a [Dragon Detector](https://github.com/valleytechsolutions/dragon-detector) into USB and its alerts join in, graded the same way. Its radar hears what scanning cannot, such as Flock cameras that only send probe requests.

Without a Dragon Detector, a Flock camera alerts when it shows its own Wi-Fi network or Bluetooth signal. Detection is best effort: a device may be quiet as you pass it.

## Deep scan

Deep scan is off until you turn it on in **Settings > Deep scan**. With it on, one spare USB Wi-Fi adapter stops scanning and listens in monitor mode while a wardrive or Recon runs. It only listens: it never sends a frame.

- **Every network it hears goes into the log.** Beacons and probe responses become the same WiGLE rows a normal scan writes, with the same columns. A network also found by a normal scan is still one device. Deep scan only adds to the wardrive.
- **Flock cameras from their probes.** Flock cameras send probe requests as they hop between channels, even with no network of their own. Deep scan uses the rules of flock-you: a probe from a Flock address prefix with the Flock fingerprint is medium evidence and alerts. Without the fingerprint it is a weak hint, unless the same sender probes on two channels that are far apart (such as 1 and 6) within 3 seconds, as Flock cameras do when they hop; then it alerts too. Probes heard on neighbouring channels do not count, because one radio can be heard a channel or two away. Frames from or to a Flock prefix are weak hints. Espressif prefixes are left out on purpose, as SquachWatch does, because your own nodes and many smart plugs use them.
- **Disconnect floods.** Within 3 seconds, 12 disconnect frames from one sender, or 6 to everyone from one sender, or 24 to everyone from any senders, alert as a deauth flood (SquachWatch's rule).
- **Evil twins.** Raw beacons feed the evil twin check. Two access points from the same maker are never called twins. One access point heard on two channels at once, or both open and protected, is added to an evil twin alert as evidence.
- **Listen pattern.** Balanced listens on channels 11, 6 and 1 for 250 ms each, as flock-you does, then the other 2.4 GHz channels by how busy they are, then the busy 5 GHz channels. Flock first stays on 11, 6 and 1, with a pass over the other 2.4 GHz channels every fourth round.
- **Which adapter listens.** Never the built-in radio, never an adapter that is connected to a network, and never the only adapter that can scan 6 GHz. Every band of the listening adapter must still be covered: 6 GHz by another adapter that scans normally, 2.4 and 5 GHz by another adapter or by nodes. With an ALFA AWUS036AXM and a second USB adapter, the second one listens and the ALFA keeps 2.4, 5 and 6 GHz. With only the ALFA, Deep scan is not available and everything scans as before. **Monitor adapter** can pick one adapter instead of Auto. The Hardware screen shows which adapter listens and why the others do not.
- **All adapters listen.** With two or more USB adapters that can listen, and Monitor adapter on Auto, Auto keeps the fewest adapters scanning normally that still cover every band any adapter has, with 6 GHz always on a 6 GHz adapter that scans normally, and every other USB adapter listens. Each listening adapter gets its own share of the channels: one keeps 11, 6 and 1 for Flock cameras, the rest go to the adapter with the least to do, busiest channels first, so busy 5 GHz channels are spread out. 6 GHz adapters also listen on the main 6 GHz channels while another 6 GHz adapter still scans normally. How many may listen (3 at the start of a drive, up to 6 later) follows what reading their frames costs the CPU, measured during the drive, so the rest of the wardrive never waits; the others keep scanning normally. A listening adapter that drops out hands its channels to the others at once and listens again 1.5 seconds after it is back. Off keeps one listening adapter, exactly as in 1.2.6.
- **Direct scans.** Adapters on USB start their own sweeps through the radio helper instead of NetworkManager, for fresher results.
- **Radio tuning.** During a wardrive, power saving goes off on USB adapters and their transmit power goes up to the limit your country allows, as the kernel reports it. Both are put back at the end. The country setting is never changed. The install keeps the 7 adapters that can listen, u-blox GPS receivers and nodes on the ESP32-C5's own USB port from going to sleep while they are plugged in.
- **Bluetooth** works exactly as before.

Deep scan needs the radio helper that the install adds: a small system service that starts only when Quartermaster asks for it, runs as its own unprivileged user with only the two network rights it needs, answers only members of the `quartermaster-radio` group and only a fixed list of commands. It has no way to change the country or to send frames, it never touches the built-in radio, and it puts every adapter back when the wardrive stops, when the app closes or crashes, and when the helper itself is stopped. After the first install, restart the Cardputer once so your user joins the new group. It can also write to the USB device nodes of the known Wi-Fi adapters, only to reset one that stopped working. If the busy street costs too much CPU with several adapters listening, they go back to normal scans one at a time. With one, Deep scan first drops data frames, then reads beacons less often, then stays on 11, 6 and 1, and at last hands the adapter back to normal scans and says so on the Hardware screen.

## Good to know

- Quartermaster only scans. It never joins a network and never pairs with a Bluetooth device.
- Record only what is legal where you are, and respect the terms of the sites you upload to.
- The phone setup page uses plain HTTP on your local network, protected by a PIN that changes every time. Open it on a network you trust.
- Network and device names are whatever their owners made them. Quartermaster treats everything it hears as untrusted, and a broken broadcast cannot stop a wardrive.
- If something does go wrong, the reason is written to `~/quartermaster/errors.log`. Attach that file when you report a problem.

## Build it yourself

```sh
git clone https://github.com/valleytechsolutions/Quartermaster
cd Quartermaster
python3 -m pip install pillow numpy pyserial pytest
python3 -m pytest tests
python3 tools/build_deb.py
```

The package lands in `build/`. That last step needs `dpkg-deb`, so run it on Debian, Ubuntu or the Cardputer itself. `python3 tools/render_screens.py` draws every screen with sample data, which is how the pictures on this page are made.

On the Cardputer, `/usr/share/APPLaunch/bin/quartermaster --probe` lists the adapters and GPS ports it can see, and whether the Cap LoRa-1262 answers.

## Credits

Recon and Drive Alerts build on this work and research:

- SquachWatch, created by Talking Sasquach, through [SquachWatch-CYD](https://github.com/skizzophrenic/SquachWatch-CYD) by skizzophrenic, GPL-3.0, at commit `4dba83c0494d789bff952f74b8b39046ff06c3c9`: the detection types with their names and order, how trackers split into Samsung, Google and Tile, fleet camera, dashcam, fleet router and smart speaker address prefixes, hotspot names, AirTag owner and HomePod adverts, evil twin, flood and burst checks, My alerts, and the LoRaWAN frame layout, NetID registry and one-alert-per-operator rule.
- [Dragon Detector](https://github.com/valleytechsolutions/dragon-detector) by Valleytech Custom Solutions, MIT: the classifier Recon is ported from, its alert rules, its tone queue, and the alerts it sends over USB.
- Flock Noir (Your Pal Kal, MIT): Flock facts.
- [flock-you](https://github.com/colonelpanichacks/flock-you) by colonelpanichacks, MIT, with research by NitekryDPaul, DeFlockJoplin and wgreenberg. Deep scan's Flock probe tiers and IE signature come from flock-you, with community research by @NitekryDPaul and DeFlockJoplin.
- Deep scan's deauth, 5 GHz slice and evil-twin logic are ported from SquachWatch-CYD (GPL-3.0).
- OUI Spy, Unified Blue and AXEOFF by colonelpanichacks, MIT: body camera research.
- [nyanBOX](https://github.com/jbohack/nyanBOX) by jbohack, MIT: AirTag and Find My adverts.
- ESP32 Marauder by justcallmekoko: Pwnagotchi and DULT details.
- The IEEE registration authority (address prefixes), the Bluetooth SIG assigned numbers, the Google Find Hub specification and OpenDroneID (ASTM F3411).

### Thanks

- [justcallmekoko](https://github.com/justcallmekoko)
- [Code_Hedge](https://github.com/CodeHedge)
- Ekoms_Savior
- Jesse Hale
- ChrisDaCrunk
- Vileer from [Hacker Gadgets](https://hackergadgets.com)
- LocoSp
- Awok ([AWOK Dynamics](https://awokdynamics.com))
- Talking Sasquach, creator of SquachWatch

## Made by Valleytech Solutions

<p align="center">
  <img src="assets/brand/vts-logo-tile.png" width="140" alt="Valleytech Solutions logo">
  &nbsp;&nbsp;&nbsp;
  <img src="assets/brand/bwm-logo-tile.png" width="140" alt="BWM logo">
</p>

<p align="center">
  Quartermaster is made by <a href="https://github.com/valleytechsolutions">Valleytech Solutions</a>.<br>
  The same two logos sail on the ship at start-up and sit on the About screen.
</p>

<p align="center">
  <img src="docs/screens/about.png" width="320" alt="The About screen, with the Valleytech Solutions and BWM logos">
</p>

## License

Quartermaster is licensed under the GNU General Public License version 3 (GPL-3.0) from version 1.2.2. See [LICENSE](LICENSE). Releases before 1.2.2 remain under the MIT License.

Copyright (c) 2026 Valleytech Custom Solutions (Your Pal Kal).

The Inter and JetBrains Mono fonts are included under the SIL Open Font License. Their license files are next to the fonts.

The Quartermaster Node firmware images include ESP-IDF and other open source parts. Their licenses are in [quartermaster/assets/node-firmware-notices.txt](quartermaster/assets/node-firmware-notices.txt).
