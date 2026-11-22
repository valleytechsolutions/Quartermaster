# Changelog

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
