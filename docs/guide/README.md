# Quartermaster user guide

This guide explains how to use Quartermaster 1.2.9 on the M5Stack CardputerZero. It is written for everyone, not only for people who know Linux or radio.

A few words used everywhere in this guide:

- **Wardrive:** a drive (or walk) where Quartermaster records the Wi-Fi networks and Bluetooth devices it hears, with their GPS positions.
- **Adapter:** a Wi-Fi radio. The Cardputer has one built in. You can plug in more on USB.
- **Log:** the file a wardrive makes. Upload sites such as WiGLE read it.
- **GPS fix:** the moment your GPS knows where you are. Nothing is logged without one.

## Start here

1. [Install and update](install.md)
2. [First start and the GPS](first-start.md)
3. [The wardrive screen and keys](wardrive.md)

## Everything else

- [Settings](settings.md): every setting, what it does and its default.
- [Wi-Fi adapters](wifi-adapters.md): which ones work, 6 GHz, "driver CD" adapters and firmware.
- [Power and USB hubs](power.md): how much power radios need, signs of trouble, Power guard.
- [Getting the most networks](more-networks.md): what helps, what does not, and how to compare two drives.
- [Deep scan and the radio helper](deep-scan.md)
- [Drive Alerts and Recon](alerts.md): warnings about cameras, trackers and more.
- [Nodes](nodes.md): extra ESP32-C5 scanners, plugged in or wireless.
- [Aircraft and LoRa mesh](aircraft-and-lora.md): ADS-B, Meshtastic, MeshCore and LoRaWAN.
- [Uploads](uploads.md): WiGLE, WDGWars and Wardrift keys, and the phone setup page.
- [Files and logs](files.md): where everything is kept.
- [Troubleshooting](troubleshooting.md): every Hardware screen message, and common problems.
- [Build a DIY Quartermaster](../diy.md): a Raspberry Pi with a Waveshare joystick screen, the same parts as Raspyjack.
- [License and credits](license.md)

## Good to know

- Quartermaster only listens and scans. It never joins a network and never pairs with a Bluetooth device.
- Record only what is legal where you are, and follow the rules of the sites you upload to.
- Every screen shows its own keys along the bottom. When in doubt, look there.
