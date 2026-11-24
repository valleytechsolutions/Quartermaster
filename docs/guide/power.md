# Power and USB hubs

[Back to the guide](README.md)

## Why power matters

The Cardputer runs on its battery, and its USB port can only give about 1 A (1,000 mA). Wi-Fi adapters tell Linux they need only 100 mA, but many really take much more. When everything together asks for more than the port gives, devices drop off, and the GPS can drop off with them.

## What things draw

These are typical estimates. Quartermaster uses the same numbers on the Hardware screen.

| Device | Typical | Peak (at start-up) |
| --- | --- | --- |
| MT7921AU tri-band adapter (ALFA AWUS036AXM and others) | 600 mA | 900 mA |
| MT7925U adapter | 650 mA | 950 mA |
| RTL8814AU adapter (ALFA AWUS1900) | 650 mA | 900 mA |
| RTL8812AU, RTL8812BU (AC1200 adapters) | 500 mA | 700 mA |
| MT7612U adapter | 450 mA | 650 mA |
| RTL8811CU / 8821CU nano adapter | 350 mA | 500 mA |
| RTL-SDR stick (aircraft) | 300 mA | 300 mA |
| AR9271 or RTL8192EU 2.4 GHz adapter | 250 mA | 400 mA |
| ESP32-C5 node | 150 mA | 350 mA |
| USB GPS | 50 mA | 50 mA |

In the summary line during a wardrive ("Adapters: ...", below), a listening adapter ([Deep scan](deep-scan.md)) counts at about 60 % of its typical draw, because it never transmits. The "USB power" estimate and Power guard count every adapter at its full typical draw.

## Use a powered hub for more than one big adapter

A **powered USB hub** has its own power supply. Devices on it take nothing from the Cardputer's port. For more than one big adapter, use a powered hub that can give **3 to 4 A**.

An **unpowered** hub only shares the Cardputer's own power. The hub itself counts as about 50 mA (100 mA at start-up). Quartermaster takes about 900 mA as the most an unpowered hub can pass on to the devices on it. When they add up to more, the Hardware screen says "Unpowered hub: about X of 900 mA" and "Use a powered USB hub for the adapters."

Some powered hubs wrongly tell Linux they have no power of their own. If yours is plugged into its power supply, set **Settings > USB hub power** to **Hub has power**. Then the devices on it are no longer counted against the Cardputer's port.

## Signs that power runs short (brownout)

- Adapters drop off and come back, or stop scanning.
- The USB hub disconnects, with everything on it.
- The GPS drops out.
- The Hardware screen says "Too many adapters for USB power", "USB power ran short" or "USB port at its limit".

The fix is almost always a powered hub, or fewer or smaller adapters.

## What the Hardware screen shows

Open the Hardware screen with **H**.

- "USB power: about X of Y mA": everything fits. "Peaks to Z mA; radios start one by one" means start-up peaks would be too much together, so Quartermaster starts them one after another.
- "Too many adapters for USB power": the estimate is over what the port gives. Use a powered USB hub.
- "Unpowered hub: about X of 900 mA": an unpowered hub is over its limit.
- "Node on the crowded hub. Run the C5 node on its own battery. It works wirelessly.": see [wireless nodes](nodes.md#wireless-nodes).
- During a wardrive, one line sums it up, for example "Adapters: 4 active, est. 2.1 A, hub: unpowered". The hub part says **powered**, **unpowered** or **none** (no hub plugged in). Below it: "Estimate. Listening adapters draw less."

## Power guard

Power guard is **on** by default (**Settings > Power guard**). When power is fine it changes nothing: every adapter scans, and the logs are the same. During a wardrive it:

1. **Starts radios one by one.** When two or more power-hungry devices (USB Wi-Fi adapters, the RTL-SDR stick, nodes) take power from the Cardputer's port or an unpowered hub, they start about 1.5 seconds apart, so their start-up peaks do not add up. An adapter plugged in during the drive then waits 1.5 seconds too. With only one of them, or with everything on a powered hub, nothing waits.
2. **Watches for trouble.** Signs are: a device restarting on the same port, an adapter that goes away and comes back, a hub dropping off, the system's under-voltage alarm, power errors in the system log, and sharp dips in battery voltage. It needs two weak signs from separate checks within 60 seconds. One strong sign is enough on its own: the system's under-voltage alarm, or a system log line about under-voltage or over-current. Unplugging something by hand does not count.
3. **Eases the extra adapters, step by step, only while trouble lasts.** First it undoes [Radio tuning](more-networks.md#radio-tuning) on them, then it spaces out their scans, then it pauses the adapter whose bands the others cover best. The adapter that keeps 6 GHz is not paused while another can be. The GPS, the built-in Wi-Fi and the nodes are never touched.
4. **Tries again.** After a few minutes of steady power it tries a paused adapter once more. If trouble comes back, that adapter stays paused while it is plugged in. Unplug it, or move it to a powered hub, and it scans again.
5. **Resets a stuck adapter.** If an adapter that worked earlier stays on USB but stops working, as after a power dip, the [radio helper](deep-scan.md#the-radio-helper) resets its USB port: up to 3 tries, after 10, 30 and 90 seconds. After that the Hardware screen says "X needs power". This needs the radio helper to be running, which only happens when Deep scan, Direct scans or Radio tuning is on. With all three off, no reset happens. The helper can only reset the 7 adapters that can listen: MT7921AU, MT7925U, MT7612U, MT7610U, RTL8812BU, RTL8811CU and AR9271. For any other adapter the reset is refused, but each refusal still counts as a try, so after 3 tries it also says "X needs power".

The wardrive screen tells you once with a short pop-up, such as "X paused: not enough USB power", "USB power ran short: extra adapters eased", "The USB port is at its limit: extra adapters eased", "Too many adapters for USB power. Use a powered hub." or "X needs power". While an adapter is paused, an amber line also stays on the wardrive screen: "X paused to save USB power. Use a powered hub." The Hardware screen shows what was eased and why. If nothing could be eased, for example with only one USB adapter, it tells you to use a powered USB hub.

Power guard never changes the country, never fakes power limits and never adds a node. A paused adapter still draws a little power while plugged in, so for many adapters the real fix is a powered hub.

## How many adapters?

A practical limit is **about 4 to 6 Wi-Fi adapters**. All USB devices share one USB 2.0 connection, and the Cardputer has a small processor that must read everything they hear. More adapters than that rarely find more networks.

To scale further, add [nodes](nodes.md). Each ESP32-C5 node scans its own share of the channels. Wireless nodes run on their own battery, so they take no power from the Cardputer at all.
