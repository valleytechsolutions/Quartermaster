# Changelog

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
