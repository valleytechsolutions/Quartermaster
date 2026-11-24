# Changelog

## 1.2.4

- Recon and Drive Alerts now use SquachWatch's detection types, with its names and in its order: Flock cameras, Axon body cams, Meta glasses, card skimmers, Raven sensors, AirTags, drones, ALPR cameras, cameras, Samsung tags, Google tags, Tile trackers, Ring cameras, deauth floods, evil twin APs, proximity beacons, hacker hardware, LoRaWAN networks, My alerts and smart speakers. Every detection rule is kept.
- Trackers are split the way SquachWatch splits them: Samsung SmartTag, Google Find Hub tags (Chipolo, Pebblebee, Moto Tag and unknown-tracker adverts) and Tile.
- In-car fleet routers are now listed under Axon body cams, as SquachWatch files them, named "fleet router" and graded as weak hints, so they never alert unless Alert from is set to weak hints. Recon does not raise "Following?" for them either, so a work truck with its own router stays quiet.
- Bursts of fake Bluetooth adverts and the Dragon Detector fox test beacon are now listed under hacker hardware. The fox is a weak hint and does not alert unless Alert from is set to weak hints.
- New: LoRaWAN networks. With the Cap LoRa-1262 and this kind turned on, a wardrive also listens to one LoRaWAN uplink channel in turn with Meshtastic and MeshCore, and alerts once for each network operator it hears, read from the device address. Off by default.
- The tone picker has only Quartermaster's own tones again: Siren, Yelp, Chirp, Shutter, Ping, Blip and Silent. The Dragon Detector melodies are gone. A kind that had one of them plays its default tone.
- Settings from earlier versions carry over: trackers become Samsung tags, Google tags and Tile, fleet routers become Axon body cams, advert bursts and the fox become hacker hardware.
- Recon's watch list shows all 20 types. Deauth floods, evil twin APs, LoRaWAN networks and My alerts are marked Wardrive only, as Recon does not look for them.
- Proximity beacons and smart speakers start off in Recon's watch list, as in SquachWatch. A watch list you already set stays.
- Thanks to ChrisDaCrunk, in the README and on the About screen.

## 1.2.3

The same app as 1.2.2 under a new number, so a CardputerZero that installed an early 1.2.2 test build also gets the calmer alerts: the banner at the bottom, Del to hide, Q for quiet, Alert position and the credits.

## 1.2.2

- A new home screen icon, drawn at the size the home screen shows it, with a bolder outline and no black square.
- More USB Wi-Fi adapters work as soon as they are plugged in. Adapters that first show up as a driver disc switch by themselves.
- When an adapter is plugged in but not scanning, the Hardware screen says why: setup mode, no driver on this system yet, a model too new for the driver, missing firmware, or not enough power.
- The README lists which Wi-Fi chips work, which do not yet, and which adapters to buy.
- Drive Alerts: during a wardrive, Quartermaster can warn you about Flock cameras, Axon body cameras, Meta camera glasses and the other kinds Recon knows. A banner shows for about 6 seconds, the Wardrive screen counts the alerts, and A lists them. Alerts are checked off the scan path, so scanning never waits, and the WiGLE log is the same with alerts on or off. Settings, Drive Alerts turns them on and off, picks the kinds, the tone for each kind, the volume, and My alerts, a list of names or MAC addresses of your own. Axon, Flock and Meta glasses alert by default.
- Alert tones: Siren, Yelp, Chirp, Shutter, Ping, Blip, the Dragon Detector melodies, or Silent. Settings, Drive Alerts plays each tone as you pick it. Tones play one at a time, the most important first, never the same tone twice within 4 seconds, and a tone that waits more than 10 seconds is dropped.
- A Dragon Detector on USB: its alerts join the wardrive's alerts, graded the same way as Recon's.
- Recon knows more: fleet routers, dashcams and Wi-Fi cameras by their network names, Sonos and other listening speakers, the Flipper Zero's company ID, and whether an AirTag is away from its owner. Fleet routers, speakers and foxes start off in Settings.
- Recon no longer calls BC:DD:C2 a Flock device: that prefix belongs to another maker.
- Recon's alert sound no longer holds up scanning.
- Quartermaster is now GPL-3.0 from this version. Earlier releases stay MIT. The README credits SquachWatch, Dragon Detector and the other projects it learns from.
- The install adds Quartermaster to the audio group, so alert tones can play.
- Alert banners now come up at the bottom, just above the key hints, for Drive Alerts and Recon alike. They never cover the counters, the live list stops above them, and messages move up out of their way.
- Calmer alerts. More of the same kind add to the banner (×3) instead of replacing it, a more urgent kind takes over at once, and alerts that pile up show as one banner such as "3 new alerts". A flood of new Flock cameras, body cameras or glasses shows one banner. The icon blinks only for the first 3 seconds.
- Flock and other fixed cameras alert once per drive, however often you pass them. The Alert again setting now applies only to body cameras and other moving devices. The log still notes every pass.
- One camera heard on Wi-Fi and Bluetooth, a Bluetooth device that keeps changing its address, such as your own glasses, and a Dragon Detector line about a camera the scan just heard are each one alert. A Dragon Detector parked by one Flock camera no longer alerts every minute.
- The body camera siren is one sweep of 1.2 seconds, plays at most every 5 minutes and 6 times an hour. More body cameras in between are counted and listed without a sound or a new banner. A kind that keeps sounding in a busy town waits longer between tones, up to 4 minutes, until it has been quiet for 10 minutes.
- N on an alert: no more alerts for that device. It is still logged and uploaded. Settings, No alerts for lists them. When the device goes by its name, anything with that name stops alerting. A device whose Bluetooth address keeps changing and that has no name, such as most Meta glasses, is silenced for this drive only.
- Recon alerts about a device that stays nearby once, and again only after it has been gone for 15 minutes or when it starts to follow you. Its beep follows Tone volume. Its banner no longer takes the R key.
- A new tone preview cuts off the one playing, so scrolling through tones keeps up.
- The Wardrive screen shows A Alerts from the start, and no longer repeats a stalled adapter warning as a message when the screen already says it.
- Del hides the alert banner at once, so the whole live list shows again. The alert stays in the list, and the next new alert still shows. Recon alerts too.
- Q on the wardrive screen, the alert list or a banner: no alert tones or banners for 10 minutes. Alerts are still listed and logged, and the wardrive screen shows Q 9m in place of the alert count, with Q End quiet in the key hints. Q again ends it.
- Settings, Alert position: Bottom or Top, for Drive Alerts and Recon. At the top of the wardrive screen the banner sits under the counters.
- No alert tones in the first minute of a wardrive. Banners and the list work as usual, and those tones are not played later.
- Tone volume starts at Medium. A volume you already picked stays.
- Thanks to justcallmekoko, Code_Hedge, Ekoms_Savior, Jesse Hale, Vileer from Hacker Gadgets, LocoSp, Awok and Talking Sasquach, the creator of SquachWatch, in the README and on the About screen.

## 1.2.1

The AppStore listing and the About screen now name every radio Quartermaster uses. Nothing else changes.

## 1.2.0

LoRa mesh nodes from the Cap LoRa-1262, and aircraft that start by themselves.

- Mesh nodes: with the Cap LoRa-1262 fitted, the Meshtastic and MeshCore nodes around you are logged during a wardrive. Quartermaster checks once, when it opens, that the radio on the cap answers. Without the cap nothing changes and nothing is slower. Settings, LoRa mesh starts on Auto: every wardrive listens when the cap was found. On always listens, Off never does. Quartermaster only listens and never sends anything on LoRa.
- Settings, Country picks the LoRa band: Meshtastic LongFast on its default channel, and MeshCore's public settings, for the United States, Canada and Mexico, Europe and the United Kingdom, Australia, and New Zealand, which has its own MeshCore settings. South Korea and Brazil have Meshtastic only. Where there are two, the radio listens to each in turn, 30 seconds at a time. The radio has a thread of its own, so Wi-Fi, Bluetooth and the screen are not slowed, and the cap's GPS keeps working.
- Each mesh node is kept once, with its closest and its furthest sighting, its signal and how many hops away it was, and the position it gives out about itself. Meshtastic names and positions are read from the public default channels only. Packets from the internet (MQTT) are skipped. A MeshCore advert only counts when its signature is right. The nodes are saved every 30 seconds, and a mesh.json.gz file is written beside the log when the wardrive stops.
- Mesh files go to WDGWars and Wardrift, never to WiGLE. WDGWars gets the nodes that give out their own position, at that position. Wardrift gets every node. Logs lists each mesh file with a MESH tag and what each site took. A site with nothing to take, such as WDGWars when no node gave its own position, gets a dash and not a red mark. When Wardrift stops part way, the nodes it already took are not sent again. A Wardrift key that cannot send mesh nodes does not stop the Wi-Fi and Bluetooth logs. The Hardware screen shows the cap, and the Wardrive screen and the summary count the mesh nodes. The Wardrive screen says when the cap stops listening. Restart once after the first install, so Quartermaster can reach the cap.
- Settings, Country also matters for the cap: Quartermaster asks for it the first time you start a wardrive with the cap fitted, if you never set it.
- Aircraft: Settings, Aircraft (ADS-B) now starts on Auto. Quartermaster listens whenever the RTL-SDR stick is plugged in, also when it is plugged in during a wardrive. On always listens, Off never does. A setting you changed yourself is kept.
- The README has a Plug in and go list: what Quartermaster finds by itself, and what still needs a press.

## 1.1.0

Nodes, wireless nodes and aircraft, and logs that keep two rows per device.

- A log now holds at most two rows per device: its closest and its furthest sighting, each with its own position and time. A sighting below -92 dBm, or with a position less sure than 25 m, is not used as the furthest one. The rows wait in a hidden file until the wardrive stops, and a log cut short by a crash or a power cut is finished when Quartermaster next opens. Settings, Rows per device, Full track logs as before.
- Quartermaster Nodes: ESP32-C5 boards on USB that scan Wi-Fi, and Bluetooth when asked, beside your adapters. The channels are shared out between them. Settings, Nodes lists them: a node that is plugged in is said once, and OK adds it. Each node can be renamed, given Bluetooth, told which light its board has and made to blink it, updated and removed.
- Settings, Flash a node puts the node firmware on an ESP32-C5 board, as a first install or an update, with a progress bar. When a board does not answer, the screen says how to put it in download mode. Flashing waits until no wardrive or Recon runs.
- A web page in `docs/flasher` puts the same node firmware on a board from Chrome or Edge on a computer, or Chrome on Android. First install sets up a new board. Update, keep settings updates a node and keeps its light, country and hub.
- Settings, Country: the nodes only use the Wi-Fi channels allowed there. It waits for a wardrive to stop, like the other scanning settings. If you use nodes, set it: it starts as United States, and Quartermaster asks the first time you open Settings, Nodes.
- The Hardware screen has a card for each node, with the channels it scans. The Wardrive screen counts the nodes next to the radios.
- Everything Quartermaster needs is installed with it, from the AppStore too: the RTL-SDR tools, NetworkManager, BlueZ and the alert sound player.
- Settings, Check for updates asks the AppStore and GitHub whether a newer version is out. It never downloads anything.
- Wireless nodes: one node stays plugged in as the hub, and up to 8 more scan on a battery and send what they hear to it by radio. Turn on Hub for wireless nodes on a node's screen, then Settings, Nodes, Add a wireless node and hold BOOT on the new node for 2 seconds. They start and stop with the wardrive and Recon, and get their own share of the channels, a little smaller because they stop to hand over what they heard.
- Wireless nodes are listed in Settings, Nodes. Their screen and the Hardware screen say when they were last heard and how well the hub hears them. One not heard for 30 seconds hands its channels to the others until it is back. Removing a wireless node makes the hub forget it. To add it again, hold BOOT on it for 2 seconds. When the hub is unplugged they wait, and the nodes on USB take every channel. Moving the hub to another node asks first, because every wireless node must then be added again. Turning the hub off or removing it says that the wireless nodes stop. A wireless node's rows get the position of the moment it heard the device, with an accuracy 50 m wider, so they are never a device's furthest row.
- Aircraft: with an RTL-SDR stick, Settings, Aircraft (ADS-B) logs the aircraft around you during a wardrive. Each is kept once, with its closest and furthest position. They go to WDGWars and Wardrift as an aircraft.json.gz file beside the log, never to WiGLE. Nothing else to install: Quartermaster brings the RTL-SDR tools, starts `rtl_adsb` only during a wardrive, at low priority on one CPU, and decodes the aircraft itself. When the air is very busy, aircraft messages are skipped rather than slow Wi-Fi and Bluetooth. A readsb or dump1090 already running is used as it is. Restart once after the first install, so the stick's TV driver stays off and you get access to the stick.
- The Hardware screen shows the stick and why it is not listening, the Wardrive screen and the summary count the aircraft, and Logs lists each aircraft file with what each site did with it. Aircraft are saved every 30 seconds, so a crash or a power cut loses at most the last 30 seconds. Aircraft heard before the GPS gives the time are moved to the GPS time once it does, as the Cardputer's own clock may be days off.

## 1.0.5

Small fixes. Nothing changes in how Quartermaster looks or is used.

- When storage refuses a write, the log being repaired is never uploaded half way through the repair.
- A log started at the very moment the uploader looks at the folder waits for the next pass instead of being sent while it is written.
- When the first part of a log has no rows and cannot be removed, the summary is named after the part that holds the rows.
- A summary line left half written by full storage no longer joins the next device into one line.
- Quitting shows the end screen when the wardrive turns out to be lost for a reason other than the one the question gave.
- Quitting during a wardrive no longer starts an upload that would be cut off as Quartermaster closes. The log uploads next time.
- Keys pressed while "Saving the wardrive…" shows, or right after Quit, are no longer acted on.
- A wardrive that has logged nothing yet, for example without a GPS fix, is no longer called saved when it is stopped.

## 1.0.4

The first update since 1.0.1. Versions 1.0.2 and 1.0.3 were test builds.

- Sym now types the symbol printed on each key of the CardputerZero keyboard. Before, a dash, an underscore and most other symbols could not be typed, so some upload keys could only be entered from a phone.
- Tab in a text box opens a list of every symbol.
- The battery level is at the top of every screen. It is green while it charges. Off the charger it turns amber at 20 % and red at 10 %.
- A notice at 20 % and at 10 %. When the battery is almost empty, Quartermaster saves a running wardrive and closes, so that the Cardputer can switch off safely. The system's own low battery warning waits while an app is open.
- The summary of a wardrive is on disk the moment the wardrive stops, so losing power right after cannot empty it.
- In a quiet place the last rows of a log no longer wait in memory until the next device is heard. They are on disk within seconds.
- Storage that is full or failing is said in plain words, on the Wardrive screen and when the wardrive ends, and such a wardrive is no longer called saved. Full storage no longer leaves half a row at the end of a log, and logging carries on in a new file as soon as there is room again.
- F, X, Z and C still move while Shift is locked, and Caps Lock on a USB keyboard is followed. Both count from the first letter, also when they were on before Quartermaster was opened.
- A key held down on a USB keyboard repeats only where that helps: moving through a list, typing and deleting. It no longer carries over to the next screen.
- In Settings, Page Up stops at the first setting and Down on the last one goes round to the first, as in every other list.
- The ` key no longer goes back, and ; . , / no longer move. Use Esc, the arrows, or F, X, Z and C.

## 1.0.1

The first release.

- Wardriving on every Wi-Fi adapter at once, built-in and USB, across 2.4, 5 and 6 GHz, with Bluetooth LE and classic beside them. Adapters can be plugged in or removed during a wardrive.
- GPS from a USB receiver, the Cap LoRa-1262, a Grove GPS unit or gpsd, found automatically.
- Each device is counted once. Its strongest and weakest sighting are both logged with their positions.
- WigleWifi-1.6 logs, written only with a GPS fix, plus a summary file per wardrive.
- Automatic uploads to WiGLE, WDGWars and Wardrift, with the result per site shown for every log.
- Upload keys entered from a phone or PC through a QR code and PIN, or typed on the device, with a test for each key.
- Recon mode for surveillance and tracking devices, with evidence grades, alerts, a follow check and a signal finder.
- Hardware screen that warns when a USB adapter is plugged in but not starting.
- Settings for scanning, logging, GPS, uploads, Recon and the screen, and a list of networks that are never logged.
- Built to keep going: names and broadcasts are treated as untrusted, a scan or screen that fails is tried again, and the reason is noted in `~/quartermaster/errors.log`.
- Made for long drives: the screen stays quick however many devices a day of driving collects, and memory use has a ceiling.
