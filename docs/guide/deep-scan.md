# Deep scan and the radio helper

[Back to the guide](README.md)

## What Deep scan is

Normally each Wi-Fi adapter **scans**: it asks the air, channel by channel, which networks are there. With Deep scan, a spare USB adapter **listens** instead, in "monitor mode": it hears every Wi-Fi message on its channel, not only the answers to a scan.

Deep scan is **off** until you turn it on in **Settings > Deep scan**. It runs during a wardrive and during Recon.

**It only listens. It never sends a single frame** (a frame is one Wi-Fi message).

## What it needs

- A USB Wi-Fi adapter whose driver supports monitor mode: MediaTek MT7921AU, MT7925U, MT7612U or MT7610U, Realtek RTL8812BU / 8822BU or RTL8811CU / 8821CU, or Atheros AR9271.
- The built-in Wi-Fi (a Broadcom chip) cannot listen this way. It always scans normally.
- Usually a **second** USB adapter or some nodes, so that every band stays covered by normal scans. See "Which adapter listens" below.
- The radio helper, which the install adds. Restart the Cardputer once after the first install.

## What it finds

- **Every network it hears goes into the log**, as the same kind of row a normal scan writes. A network also found by a normal scan still counts once. Deep scan only adds.
- **Flock cameras from their probes.** Flock cameras send short "probe requests" as they hop between channels, even when they show no network of their own. A normal scan cannot hear these. A probe from a Flock address with the Flock fingerprint is medium evidence and alerts. Without the fingerprint it is a weak hint, unless the same sender is heard on two channels far apart (such as 1 and 6) within 3 seconds, as Flock cameras do when they hop. Then it alerts too.
- **Disconnect floods (deauth).** Any one of these, within 3 seconds, alerts as a deauth flood: 12 disconnect messages from one sender, or 6 messages "to everyone" from one sender, or 24 messages "to everyone" from any senders together. Both kinds of disconnect message count. After an alert, the next one waits at least 15 seconds. Such floods can be an attack that knocks people off their Wi-Fi.
- **More evidence for evil twins.** An evil twin is one network name that is open on one access point and protected on another, which can be a fake network set up to trick people. The evil twin check runs on every normal sweep, with or without Deep scan. Deep scan adds evidence to such an alert, such as the same access point heard on two channels at once, or both open and protected. On its own, this never alerts. Two access points from the same maker are never called twins.

The wardrive screen shows **DEEP** while it listens, and **F** with a number: how many times it heard Flock frames. This is not a count of cameras. One camera can raise it several times. The Hardware screen shows the same number as "N Flock".

## Listen pattern

**Settings > Listen pattern**:

- **Balanced** (default): channels 11, 6 and 1 for 250 ms each, then the other 2.4 GHz channels by how busy they are, then the busy 5 GHz channels.
- **Flock first**: stays on 11, 6 and 1, with a pass over the other 2.4 GHz channels every fourth round.

A single listening adapter never listens on 6 GHz. With **All adapters listen** (below), listening adapters that can do 6 GHz also listen on the main 6 GHz channels, but only while another 6 GHz adapter still scans normally.

## Which adapter listens

- Never the built-in Wi-Fi.
- Never an adapter that is connected to a network.
- Never the only adapter that can scan 6 GHz.
- Every band of the listening adapter must still be covered. 6 GHz must always be scanned normally by another adapter. For 2.4 and 5 GHz, nodes count too, so one USB adapter plus nodes can be enough.

Example: with an ALFA AWUS036AXM and a second USB adapter, the second one listens and the ALFA keeps 2.4, 5 and 6 GHz. With only the ALFA, Deep scan is not available and everything scans as before.

**Settings > Monitor adapter** can pick one adapter instead of **Auto**.

### All adapters listen

With **Settings > All adapters listen** on **Auto** (the default), **Settings > Monitor adapter** on **Auto**, and two or more USB adapters that can listen, Quartermaster keeps the fewest adapters scanning normally that still cover every band, with 6 GHz always on a 6 GHz adapter that scans normally. Every other USB adapter listens on its own share of the channels. One keeps 11, 6 and 1 for Flock cameras. The busy 5 GHz channels are spread out over the rest.

At the start of a drive, at most 3 adapters listen. Once Quartermaster has measured how much processor time reading their frames costs, the limit can rise to 6. Nodes do not count toward band coverage in this mode. The rest keep scanning normally. A listening adapter that drops out hands its channels to the others at once, and listens again 1.5 seconds after it is back.

**Off** keeps one listening adapter. If you pick one adapter in **Monitor adapter**, only that adapter listens, whatever this setting says.

### When the processor gets busy

On a very busy street, listening adapters go back to normal scans one at a time. With a single listening adapter, Deep scan first skips data frames, then reads beacons less often, then switches to the Flock first pattern (11, 6 and 1, with a pass over the other 2.4 GHz channels, and no 5 GHz), and at last hands the adapter back to normal scans. The Hardware screen says so, for example "Busy CPU: Flock channels only".

## On the Hardware screen

A listening adapter shows **MON**. Its line says what it listens on, how many frames per second it reads and how many it dropped, for example "Deep scan: ch 11·6·1 + 2.4 + 5 GHz busy". Adapters that scan normally say why, for example:

| Message | Meaning |
| --- | --- |
| Built-in: always scans normally | The built-in Wi-Fi never listens. |
| Only 6 GHz radio: scans normally | It keeps 6 GHz covered. |
| Keeps 6 GHz | It keeps 6 GHz covered while another adapter listens. |
| No monitor mode in this driver | This adapter cannot listen. |
| Connected to a network: left alone | It is in use for your internet connection. |
| Needed to keep its bands scanned | No other adapter covers its bands. |
| Keeps its bands in normal scans | Chosen to cover its bands normally. |
| Another adapter listens | One listener is enough (All adapters listen is Off). |
| Not the adapter picked in Settings | You chose another Monitor adapter. |
| Listen limit for this CPU reached | More listeners would slow the wardrive. |
| Paused to save USB power | Power guard paused it. |
| Could not listen: tried again soon | The adapter refused. It is tried again. |
| Starts listening in a moment | It is about to listen. |

When Deep scan cannot run at all, a **DEEP** line says why, for example "Needs a USB Wi-Fi adapter with monitor mode", "Needs a second USB adapter; X keeps 6 GHz" or "No spare adapter can listen without losing a band".

## The radio helper

Listening, Direct scans, Radio tuning and resetting a stuck adapter need system rights that an app should not have. The install adds a small **radio helper** that does only those steps.

What the install adds:

- the helper itself, `quartermaster-radio` (a system service with a socket at `/run/quartermaster/radio.sock`),
- a group called **quartermaster-radio**, with every normal user added to it, so your Quartermaster may talk to the helper,
- a group called **quartermaster-usbreset**, which only the helper is in,
- a rule that keeps some devices from going to sleep while plugged in: the 7 adapters that can listen (MT7921AU, MT7925U, MT7612U, MT7610U, RTL8812BU, RTL8811CU and AR9271), u-blox GPS receivers, and nodes plugged in by the ESP32-C5's own USB port. Other adapters, other GPS receivers and nodes on a USB-serial chip are not covered. The rule also lets the helper reset those 7 adapters, and no others.

How it is kept safe:

- It starts only when Quartermaster asks for it. Nothing listens until you turn on Deep scan and start a wardrive or Recon.
- It runs as its own user without special rights, except the two network rights it needs and the quartermaster-usbreset group, which lets it reset the 7 known adapters after a brownout.
- It answers only members of the quartermaster-radio group, and only a fixed list of commands.
- It has no way to change the country or to send frames, and it never touches the built-in Wi-Fi.
- It puts every adapter back when the wardrive stops, when the app closes or crashes, and when the helper itself stops.

**Restart the Cardputer once after the first install**, so that your user joins the new group. Until then, Settings and the Hardware screen say "Restart the Cardputer once so the new permission applies."

Other helper messages:

| Message | What to do |
| --- | --- |
| Radio helper not installed. Update Quartermaster from the AppStore. | The helper is missing. Install or update Quartermaster. |
| Radio helper failed: ... | Something went wrong. Restart the Cardputer. If it stays, report it with `errors.log` (see [Files and logs](files.md)). |
| Starts with the next wardrive or Recon. | All is fine. |
| The radio helper stopped. Scanning normally. | The wardrive goes on with normal scans. |

## On a DIY Raspberry Pi

Deep scan works the same way on a Pi. The Pi package and the ready SD card images have the radio helper.

- The built-in Wi-Fi of a Pi never listens either. Deep scan needs a USB adapter from the list above. On a Zero, plug the adapters into an OTG hub.
- On a ready image, Quartermaster's own account is already in the **quartermaster-radio** group, so you do not need to restart for it. After a manual install, the package adds your user to the group, and the restart at the end of the [DIY guide](../diy.md#assemble-and-install-manually) makes it apply.
- Until that restart, Settings and the Hardware screen say "Restart the Pi once so the new permission applies." Restart the Pi with `sudo reboot`.
- When the helper is missing, they say "Radio helper not found. Reinstall the Pi package from GitHub." First switch the helper on with `sudo systemctl enable --now quartermaster-radio.socket`. If the message stays, reinstall the Pi package as [Troubleshooting](troubleshooting.md#on-a-diy-raspberry-pi) shows.
