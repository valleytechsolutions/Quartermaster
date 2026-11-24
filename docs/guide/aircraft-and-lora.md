# Aircraft and LoRa mesh

[Back to the guide](README.md)

## Aircraft (ADS-B)

Aircraft send out their position with a signal called ADS-B. With an **RTL-SDR stick** (a cheap USB radio with the RTL2832U chip), Quartermaster logs the aircraft around you, for **WDGWars and Wardrift**. WiGLE does not take aircraft, so they are never sent there.

1. Nothing to install. The radio tools (the `rtl-sdr` package) come with Quartermaster. They add no service, so nothing runs until a wardrive.
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
