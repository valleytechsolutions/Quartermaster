# Troubleshooting

[Back to the guide](README.md)

Start with the **Hardware** screen (**H** on the menu). It lists everything that is plugged in, and says in plain words when something is wrong. Lines in amber need your attention. Scroll with **F** and **X**.

When something goes wrong inside the app, the reason is written to `~/quartermaster/errors.log`. Attach that file when you report a problem.

In a terminal on the Cardputer, or over SSH, this command lists the adapters and GPS ports Quartermaster can see, and whether the Cap LoRa-1262 answers:

```sh
/usr/share/APPLaunch/bin/quartermaster --probe
```

## Hardware screen messages

### Wi-Fi adapters that do not start

An adapter that is plugged in but has not started after 10 seconds gets a line with its name and one of these:

| Message | What to do |
| --- | --- |
| Wi-Fi adapter in setup mode. It shows as a driver disc. Replug it. | Unplug it and plug it in again. See ["Driver CD" adapters](wifi-adapters.md#driver-cd-adapters). |
| No driver for it here. It needs Linux X. | This chip needs a newer Linux than the Cardputer has. Use another adapter for now. |
| This system has no driver for it. | This chip has no driver in this Linux. See [Wi-Fi adapters](wifi-adapters.md). |
| This model is too new for the driver here. | The chip is supported, but this model came out later than Linux 6.18. |
| Firmware missing. Install X. | Run `sudo apt install X` while online, then replug the adapter. |
| Firmware missing. This system needs newer firmware. | The firmware package is there but too old for this adapter. |
| Needs more power. Use a powered hub. | See [Power and USB hubs](power.md). |
| On USB but not starting. Replug it or use a powered hub. | Replug it. If it happens again, use a powered hub. |

The wardrive screen also says "X is not starting. See Hardware."

### Wi-Fi adapters that work

| Message | Meaning |
| --- | --- |
| Bands (2.4, 5, 6 GHz) and a number of sweeps | It scans. |
| No usable bands reported | The adapter reports no band it can use. Replug it. |
| Paused to save USB power | Power guard paused it. See [Power](power.md). |
| Tuning undone to save USB power | Power guard turned Radio tuning off on it. |
| Power save off · tx N dBm (limit N) | Radio tuning is on for it. |
| An error after the adapter name | The last scan failed. It tries again by itself. |
| No Wi-Fi adapter found. Bluetooth still scans. Plug in a USB adapter any time. | No Wi-Fi at all. |

Deep scan lines (**MON** and **DEEP**) are explained in [Deep scan](deep-scan.md#on-the-hardware-screen).

### USB power

| Message | Meaning |
| --- | --- |
| USB power: about X of Y mA. Estimate. Within what the Cardputer's port gives. | All fits. |
| USB power: about X of Y mA. Estimate. Peaks to Z mA; radios start one by one. | It fits, but start-up peaks would not, so radios start one by one. |
| Too many adapters for USB power | Use a powered USB hub. |
| Unpowered hub: about X of 900 mA | That hub is overloaded. Use a powered hub. |
| Node on the crowded hub | Run the node on its own battery, as a [wireless node](nodes.md#wireless-nodes). |
| Adapters: N active, est. X A, hub: powered / unpowered / none | The summary line during a wardrive. "none" means no hub is plugged in. Its detail line says "Estimate. Listening adapters draw less." |
| USB power ran short (adapters eased, or N adapters paused) | Power guard saw trouble. The reason follows, for example "The Pi reports under-voltage", "USB over-current", "The USB hub dropped off with its devices", "The USB hub lost a device" or "Battery voltage dipped under load". |
| USB port at its limit | The USB port ran out of channels, or adapters show USB errors (-71). Extra adapters are eased. |
| X. Dropped out. USB reset N of 3. | An adapter stopped working and is being reset. |
| X needs power. Reset 3 times, still off. Use a powered USB hub. | Move it to a powered hub. |

### Nodes

| Message | Meaning |
| --- | --- |
| New node found. Add it under Settings > Nodes. | Press **OK** on it in Settings > Nodes. |
| Ready. It scans in the next wardrive or Recon. | All is fine. |
| Channels (ch 1 6 11 ...) and sweeps | It scans. |
| Hub for N wireless nodes. It does not scan. | This node is the hub. |
| Old firmware. Update it under Settings > Nodes. | Update it. |
| Being updated | Wait. |
| Not plugged in | Plug it in, or remove it from the list. |
| Other node messages | See [Node messages](nodes.md#node-messages). |

### GPS

| Message | Meaning |
| --- | --- |
| The GPS name and your position, with HDOP | It has a fix. |
| No fix yet. It needs a view of the sky. | Move it near a window, or outside. |
| X found. Waiting for it to send a position. | A USB GPS is plugged in but sends nothing yet. Wait, or replug it. |
| No GPS found. Plug in a USB GPS, the LoRa cap or a Grove GPS. | Plug one in. Check **Settings > GPS source**. |
| GPS is off in Settings. Rows cannot be logged without a position. | Set **Settings > GPS source** to **Auto**. |

### Bluetooth

| Message | Meaning |
| --- | --- |
| Scans Bluetooth LE and classic | All is fine. |
| Bluetooth scan is off in Settings | Turn **Settings > Bluetooth scan** on. |
| No Bluetooth adapter found. Bluetooth devices cannot be logged. | The built-in Bluetooth is not available. Restart the Cardputer. |

### Aircraft and LoRa

See the message tables in [Aircraft and LoRa mesh](aircraft-and-lora.md).

- The Hardware screen shows no **A Aircraft** key: the stick cannot listen now. Press **A** anyway and it says why.
- "This wardrive does not listen for aircraft": **Settings > Aircraft (ADS-B)** is Off, or no stick was found. Stop the wardrive to change the setting.
- The viewer shows an aircraft with dashes or "Unknown": it has not sent that field yet. Position needs two messages, so it comes a few seconds after the callsign.
- No distance in the list: there is no GPS fix, or the aircraft sent no position in the last minute.
- Heading says "Unknown" at 0 kt: an aircraft that stands still has no direction.
- "The time is not known yet. Wait for a GPS fix.": a capture needs the time. Wait until the GPS has a fix, then press **S** again.
- "Capture ended: storage is almost full" or "Capture ended: it reached 128 MB": what was captured until then is saved.

## Wardrive screen messages

| Message | What to do |
| --- | --- |
| Waiting for GPS fix | Normal at the start. See [First start](first-start.md#waiting-for-a-gps-fix-nothing-is-logged-until-then). |
| No GPS found. Logging pauses until one has a fix. | Plug in a GPS. |
| GPS fix lost. Logging pauses until it is back. | Give the GPS a view of the sky. |
| GPS fix is too weak. Logging waits for a better one. | Wait, or lower **Settings > GPS accuracy needed**. |
| X is not starting. See Hardware. | See the adapter table above. |
| X paused to save USB power. Use a powered hub. | See [Power](power.md). |
| The LoRa cap stopped listening. See Hardware. | It restarts by itself. |
| LoRa uses the X band. Set your country in Settings. | Choose your country. |
| No speaker found: alerts show on screen only | Alerts still show. |
| Bluetooth is switched off: run sudo rfkill unblock bluetooth | Run that command in a terminal. |
| Bluetooth adapter is not ready: run sudo rfkill unblock bluetooth | Run that command in a terminal. |
| No Bluetooth permission: reboot once after installing | Restart the Cardputer once. |
| Bluetooth service (bluetoothd) is not running | Restart the Cardputer. |
| Cannot write the log: ... | Storage is full or broken. Free some space. |
| Some files of this wardrive could not be removed | A wardrive was thrown away, but storage kept some of its files. They can come back as a log at the next start. Delete it on the Logs screen. |

## Common problems

**The wardrive will not start.**

- "Storage is almost full (under 32 MB free)": delete old logs (Logs, open one, **D**), or copy them off and delete them.
- "No Wi-Fi adapter found and Bluetooth is off": plug in an adapter, or turn **Settings > Bluetooth scan** on.
- "Stop Recon first?": Recon and a wardrive never run together. Confirm to switch.

**Nothing is logged.** The GPS has no fix yet. Nothing is counted or logged until it has. See [First start and the GPS](first-start.md).

**The summary says "Nothing: GPS never had a fix".** The wardrive was stopped before the first fix. Start again once the GPS has one.

**Adapters keep dropping off, or the hub disconnects.** Not enough power. Use a powered hub of 3 to 4 A, fewer adapters, or smaller ones. See [Power and USB hubs](power.md).

**Messages ask you to restart once.** The install added permissions that only apply after a restart. Restart the Cardputer once.

**The phone setup page says "Not on a network".** Join Wi-Fi in the CardputerZero settings first. The screen updates by itself.

**Uploads do not go out.** Check the Uploads screen (**U**). See [Uploads](uploads.md#common-upload-messages).

**The AppStore shows "not found" right after a release.** This has been seen when a new version had just come out. Wait a few minutes and try again.

**Some features do not work after installing with dpkg.** Install with `sudo apt install -y /tmp/quartermaster.deb`, or run `sudo apt install -f`. See [Install](install.md#use-apt-not-dpkg).

**"That screen hit a problem. Back at the menu."** A screen failed. Scanning goes on. If it happens again, send `~/quartermaster/errors.log`.

**"The settings could not be saved."** The home folder may be full or read-only. Free some space.

**Deep scan does not listen.** Check the **DEEP** line on the Hardware screen. Most often you need a second USB adapter that can listen, or a restart after the first install. See [Deep scan](deep-scan.md).

## On a DIY Raspberry Pi

See also [Troubleshooting](../diy.md#troubleshooting) in the DIY guide.

**The Hardware screen** shows one device at a time. Move the joystick up and down to go through them.

**Commands.** [Log in to the Pi](../diy.md#log-in), then use these:

| What | On a ready SD card image | After a manual install |
| --- | --- | --- |
| What Quartermaster can see | `quartermaster --probe` | `quartermaster --probe` |
| The reason, when something goes wrong | `sudo cat ~/quartermaster/errors.log` | `cat ~/quartermaster/errors.log` |
| The app's own log | `sudo journalctl -b -u quartermaster@_quartermaster.service` | `journalctl -b -u "quartermaster@$USER.service"` |

`quartermaster --probe` lists the adapters and GPS ports. On a Pi it has no line for the Cap LoRa-1262.

**Messages that are different on a Pi:**

| Message | What to do |
| --- | --- |
| No GPS found. Plug in a USB GPS or connect a receiver through gpsd. | Plug in a USB GPS. On a Zero, use the port marked **USB**. Check **Settings > GPS source**. |
| USB power: about X of Y mA. Estimate. Within what the Pi's USB ports give. | All fits. |
| Connect the Pi and phone to the same network. | The phone setup screen says this while the Pi is not on a network. See [Uploads](uploads.md#on-a-diy-raspberry-pi). |

**Messages that ask you to restart, update or reinstall.** On a Pi, do this:

| Where a message says | What to do on a Pi |
| --- | --- |
| Restart the Pi once | Restart the Pi with `sudo reboot`. |
| Update Quartermaster | Install the newest Pi package, as **Updating** under [Good to know](../diy.md#good-to-know) in the DIY guide shows. Run only step 3 of the manual install, then restart the Pi. |
| Reinstall Quartermaster | Run the commands of step 3 of the [manual install](../diy.md#assemble-and-install-manually), but type the last one with `--reinstall`: `sha256sum --check SHA256SUMS && sudo apt install --reinstall ./quartermaster-pi_1.2.11_all.deb`. Without it, apt does nothing when this version is already installed. Then restart the Pi. |
| Radio helper not found. Reinstall the Pi package from GitHub. | Switch the radio helper on with `sudo systemctl enable --now quartermaster-radio.socket`. If the message stays, reinstall the Pi package as the line above shows. |

**Quartermaster closed.** After **Quit** on the menu, Quartermaster stays closed until the Pi starts again. Restart the Pi to open it. If Quartermaster stops because of an error, it opens again by itself after a few seconds.
