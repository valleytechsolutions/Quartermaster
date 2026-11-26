# Aircraft and LoRa mesh

[Back to the guide](README.md)

## Aircraft (ADS-B)

Aircraft send out their position with a signal called ADS-B. With an **RTL-SDR stick** (a cheap USB radio with the RTL2832U chip), Quartermaster logs the aircraft around you, for **WDGWars and Wardrift**. WiGLE does not take aircraft, so they are never sent there.

1. Nothing to install. The radio tools (the `rtl-sdr` package) come with Quartermaster. They add no service, so nothing runs until a wardrive listens or you open the [aircraft viewer](#aircraft-viewer).
2. Restart the Cardputer once after the first install. This keeps the TV driver off the stick and gives you access to it.
3. Plug in the stick, before or during a wardrive.
4. Start a wardrive. The wardrive screen counts the aircraft, and the Hardware screen shows the stick.

**Settings > Aircraft (ADS-B)**:

- **Auto** (default): listens whenever a stick is plugged in.
- **On**: always listens, and says when the stick is missing.
- **Off**: never listens.

During a wardrive Quartermaster starts `rtl_adsb` (the decoder from rtl-sdr) at a low priority and stops it with the wardrive. Wi-Fi and Bluetooth come first: when the air is very busy, some aircraft messages are skipped rather than slow the wardrive.

**You do not need to install readsb.** Quartermaster uses `rtl_adsb`, which comes with it. Only when `rtl_adsb` is missing does it start a readsb, and only one built with RTL-SDR support. On the CardputerZero, Debian's readsb package has been found to lack RTL-SDR support, so it cannot open the stick. If a readsb or dump1090 that you set up yourself already runs, and its `aircraft.json` in `/run` is up to date, Quartermaster reads that and does not start anything.

Each aircraft is kept once, with its closest and its furthest position. When the wardrive stops, an `.aircraft.json.gz` file goes beside the WiGLE log and is sent to WDGWars and Wardrift. Wardrift only gives points for aircraft heard in the last 3 days, so keep uploads on.

What the Hardware screen says about the stick:

| Message | Meaning |
| --- | --- |
| Ready. It listens in the next wardrive. | All is fine. |
| Running | It is listening now. |
| Uses the aircraft program that already runs | A readsb or dump1090 of your own is used. |
| Not plugged in | No stick found (with the setting on On). |
| Off in Settings | The setting is Off. |
| No permission yet. Restart the Cardputer once. | Restart once after installing. |
| Restart the Cardputer once to use it | The TV driver still holds the stick. Restart once. |
| In use by another program | Close the other program that uses the stick. |
| rtl-sdr is missing. Reinstall Quartermaster | Reinstall with `apt` (see [Install](install.md)). |
| It stopped. Trying again soon. | It restarts by itself. |

## Aircraft viewer

The viewer shows the aircraft the stick hears right now. It works with or without a wardrive.

1. Open **Hardware** (press **H** on the menu).
2. When the RTL-SDR card says "Ready" or "Running", the bottom line shows **A Aircraft**. Press **A**. On a Raspberry Pi screen, press **K3** and choose **Aircraft**.
3. When the stick cannot listen, **A** tells you why, with the same messages as the table above.

**The list.** One row per aircraft: its callsign (or its ICAO code in blue when it sent no callsign), height in feet, speed in knots, distance and direction from you, and how long ago it was last heard. The nearest aircraft is first. Aircraft with no known distance follow, the latest first. **Tab** changes the order to the latest first. Distance needs a GPS fix and a position from the aircraft in the last minute. A dash means "not heard".

**One aircraft.** Press **OK** on a row. You see everything it sent: ICAO code, callsign, squawk, category, barometric and GNSS height, ground speed, heading, vertical rate, position, distance from you, message count, and when it was first and last heard. A field the aircraft did not send says "Unknown". Quartermaster never fills in a guess. An aircraft that stands still has a ground speed of 0 kt and no direction, so its heading says "Unknown". An aircraft heard again after five minutes of silence starts afresh: only what it sends from then on is shown. Signal strength says "Unknown" with `rtl_adsb`, which does not report it.

**The route.** Press **OK** again. The map shows the track the aircraft flew while you heard it. North is up. You are the green cross in the middle, and the two rings are the distances written beside the map, such as "Rings 25 / 50 km". The gold dot is the newest position, with a short line for its heading. The list beside it has the positions, newest first. Where the aircraft was not heard for two minutes, the line has a gap. Without a GPS fix the track is centred instead. The map keeps up to 240 positions of an aircraft, at most one a second. On a long flight they are kept further apart, and the line stays whole. The "Route" line of an aircraft counts every position heard, and a capture has every position heard while it runs.

On a Raspberry Pi screen the map fills the screen. Move the stick down for the positions, and up again for the map. **Save capture**, **Sort** and **Captures** are under **K3** in the list.

Origin and destination are not sent by aircraft. The route is only the part of the flight you heard.

### Saving a capture

Press **S** in the list to start a capture. Everything still in memory goes into it first: the track of every aircraft in the list and the last 20,000 frames. From then on every position and every frame is written to disk. Press **S** again to end it. Leaving the viewer ends it too. Press **L** to see the captures, and **D** there to delete one.

A capture is two files in `~/quartermaster/aircraft`:

| File | What is in it |
| --- | --- |
| `quartermaster_<date>-<time>.track.csv` | One line per position: `Time` (UTC), `ICAO`, `Callsign`, `Latitude`, `Longitude`, `AltitudeFt`, `SpeedKt`, `Heading`, `VerticalRateFpm`, `Squawk`. A field not heard is empty. |
| `quartermaster_<date>-<time>.frames.avr` | Every raw Mode S frame heard, one per line, in the AVR format with timestamps: `@`, 12 hex digits of time, the frame in hex, `;`. |

The time in an AVR line counts in steps of 1/12,000,000 second from the second in the file name (UTC). dump1090 and readsb read these lines on their raw input port, for example `nc localhost 30001 < file.frames.avr`.

Good to know:

- Times are UTC from the GPS. The Cardputer has no battery clock, so the time the GPS gave is kept for as long as Quartermaster runs, also when the fix is lost.
- Before the GPS has told the time, the Cardputer's own clock is used. When the GPS then tells the time and the two differ by 2 seconds or more, the capture is moved to the GPS's time: its files get that time in their name, and every time in them moves by the same number of seconds.
- With no GPS time and a clock that was never set, a capture does not start. It says "The time is not known yet. Wait for a GPS fix."
- The files are written under hidden names and put on disk every few seconds. After a crash or a power cut, the capture is finished the next time the viewer opens.
- A capture ends by itself at 128 MB, or when storage is almost full.
- Captures are never uploaded.
- When a readsb or dump1090 of your own holds the stick, the viewer shows its aircraft. There are no raw frames then, so a capture has only the track file.

### The viewer and a wardrive

One stick has one reader. During a wardrive that listens for aircraft, the viewer reads the same frames the wardrive hears and starts nothing, so the wardrive is not slowed and loses no aircraft. A wardrive with **Aircraft (ADS-B)** set to Off does not listen, and the viewer says "This wardrive does not listen for aircraft". Starting a wardrive closes the viewer first, so the wardrive gets the stick.

## LoRa mesh: Meshtastic and MeshCore

With the **M5Stack Cap LoRa-1262** fitted, Quartermaster also logs the Meshtastic and MeshCore nodes around you (small long-range radios people use to chat without phone networks), for **WDGWars and Wardrift**. WiGLE does not take them. Quartermaster only listens. It never sends anything on LoRa.

1. Nothing to install.
2. Restart the Cardputer once after the first install.
3. Fit the cap **before** you switch on. Quartermaster checks once, when it opens, that the radio on the cap answers.
4. **Settings > LoRa mesh** is on **Auto**, so every wardrive listens when the cap was found. **On** always listens and says when the cap is missing. **Off** never listens.
5. Set **Settings > Country**. Quartermaster asks the first time you start a wardrive with the cap.

The country picks the band:

| Country | Meshtastic (LongFast, default channel) | MeshCore (public) |
| --- | --- | --- |
| United States, Canada, Mexico | 906.875 MHz | 910.525 MHz |
| Europe and the United Kingdom | 869.525 MHz | 869.618 MHz |
| Australia | 919.875 MHz | 915.800 MHz |
| New Zealand | 919.875 MHz | 917.375 MHz |
| South Korea | 922.875 MHz | |
| Brazil | 903.875 MHz | |

The cap cannot reach the bands of other countries (such as Japan, Taiwan or India), and has no band for the Country choice **Anywhere else**. There it does not listen, and the Hardware screen says "No LoRa band for this country". One radio cannot hear Meshtastic and MeshCore at the same time, so where there are both, Quartermaster listens to each in turn, 30 seconds at a time.

What is kept:

- **Meshtastic:** every node heard on the default channel. Its name, role and position come only from the public default channel. Messages that came over the internet (MQTT) are skipped.
- **MeshCore:** nodes that send an advert with a correct signature.
- Each node once, with its closest and furthest sighting, the signal and how many hops away it was.

When the wardrive stops, a `.mesh.json.gz` file goes beside the WiGLE log:

- **WDGWars** gets each node at the position the node gives out about itself. Nodes that give no position are not sent.
- **Wardrift** gets every node, with its position when it gives one.

What the Hardware screen says about the cap:

| Message | Meaning |
| --- | --- |
| Ready. It listens in the next wardrive. | All is fine. |
| The channel and frequency, in green, such as "Meshtastic LongFast 906.875 MHz", with a packet count | It is listening now, on that channel. |
| Starting | The listener is starting. |
| Off in Settings | **LoRa mesh** is set to Off. |
| Set your country in Settings. | Choose a country first. |
| Looking for it | It is being checked. |
| Not found. Plug it in, then switch on. | Fit the cap with the Cardputer off. |
| No permission yet. Restart once. | Restart once after installing. |
| In use by another app | Close the other app. |
| Parts are missing. Reinstall Quartermaster. | Reinstall with `apt`. |
| No LoRa band for this country | The cap cannot listen in your country. |
| It stopped. Trying again soon. | It restarts by itself. |

The wardrive screen may say "LoRa uses the US 915 band. Set your country in Settings." until you choose a country.

## LoRaWAN networks

LoRaWAN is used by sensors and meters that report over long distances. Turn on **LoRaWAN networks** in **Settings > Alert me about**, and keep **Settings > Alerts while wardriving** on (it is on by default). If Drive Alerts are off, LoRaWAN is not listened to. Then, while you wardrive with the cap, Quartermaster also listens for LoRaWAN messages for 30 seconds after each Meshtastic and MeshCore turn. It is off by default.

Each message carries a device address, and the start of that address names the network operator, such as The Things Network, Helium or a utility. Quartermaster alerts once per operator, not once per device. The content stays encrypted and is never read. LoRaWAN devices are not mesh nodes, so they are not written to the mesh file and are not uploaded.

Limits: it listens on one channel and one data rate only (903.9 MHz in the US, 868.1 MHz in Europe, 916.8 MHz in Australia, New Zealand and Brazil, 922.1 MHz in South Korea), so networks that use other channels or slower rates may not be heard.
