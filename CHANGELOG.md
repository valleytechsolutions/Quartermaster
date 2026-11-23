# Changelog

## 1.1.0

- A log now holds at most two rows per device: its closest and its farthest sighting, each with its own position and time. A far sighting below -92 dBm or less sure than 25 m is left out. The rows wait in a hidden file until the wardrive stops, and a log cut short by a crash or a power cut is finished at the next wardrive. Settings, Rows per device, Full track logs as before.
- Quartermaster Nodes: ESP32-C5 boards on USB that scan Wi-Fi, and Bluetooth when asked, beside your adapters. The channels are shared out between them. Settings, Nodes lists them: a node that is plugged in is said once, and OK adds it. Each node can be renamed, given Bluetooth, made to blink its light, updated and removed.
- Settings, Flash a node puts the node firmware on an ESP32-C5 board, as a first install or an update, with a progress bar. When a board does not answer, the screen says how to put it in download mode. Flashing waits until no wardrive or Recon runs.
- Settings, Country: the nodes only use the Wi-Fi channels allowed there. It waits for a wardrive to stop, like the other scanning settings.
- The Hardware screen has a card for each node, with the channels it scans. The Wardrive screen counts the nodes next to the radios.
- Settings, Check for updates asks the App Store and GitHub whether a newer version is out. It never downloads anything.
- Wireless nodes: one node stays plugged in as the hub, and up to 8 more scan on a battery and send what they hear to it by radio. Turn on Hub for wireless nodes on a node's screen, then Settings, Nodes, Add a wireless node and hold BOOT on the new node for 2 seconds. They start and stop with the wardrive and get their own share of the channels, a little smaller because they stop to hand over what they heard.
- Wireless nodes are listed in Settings, Nodes. Their screen and the Hardware screen say when they were last heard and how well the hub hears them. One not heard for 30 seconds hands its channels to the others until it is back. When the hub is unplugged they wait, and the nodes on USB take every channel. Moving the hub to another node asks first, because every wireless node must then be added again.
- Aircraft: with an RTL-SDR stick and readsb (`sudo apt install readsb`), Settings, Aircraft (ADS-B) logs the aircraft around you during a wardrive. Each is kept once, with its closest and furthest position. They go to WDGWars and Wardrift as an aircraft.json.gz file beside the log, never to WiGLE. A readsb that already runs is used as it is; otherwise one is started for the wardrive at low priority and stopped with it.
- The Hardware screen shows the stick and why it is not listening, the Wardrive screen and the summary count the aircraft, and Logs lists each aircraft file with what each site did with it. Aircraft are saved every 30 seconds, so a crash or a power cut loses at most the last 30 seconds.

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
