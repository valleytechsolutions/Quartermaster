# Getting the most networks

[Back to the guide](README.md)

## The basics first

These matter more than anything else:

- **A good GPS fix.** Nothing is logged without one. See [First start and the GPS](first-start.md).
- **Adapters that work.** Check the Hardware screen (**H**) for amber lines before you drive.
- **Enough power.** Adapters that brown out miss networks. See [Power and USB hubs](power.md).
- **Settings that keep everything.** Leave **Wi-Fi bands kept** on **All**, **Weakest signal kept** on **Everything**, **Pause between sweeps** on **None** and **Hidden networks** on **Log**. These are the defaults.

**Rows per device** does not change how many networks you find. **Full track** only adds more rows for the same networks.

## How the bands are shared

- **Adapters in the Cardputer** each sweep every band they support. A tri-band adapter covers 2.4, 5 and 6 GHz in one sweep, while the built-in Wi-Fi keeps sweeping 2.4 GHz beside it. Quartermaster gives each adapter a full sweep of its bands.
- **Nodes** split the channels between them, so with more nodes each channel is heard more often. See [Nodes](nodes.md).
- **Listening adapters** (Deep scan) also split the channels between them, each on its own share.
- **Wi-Fi bands kept** only leaves networks out of the log. It never finds more.

For 6 GHz you need a Wi-Fi 6E adapter (MT7921AU or MT7925U). See [Wi-Fi adapters](wifi-adapters.md#6-ghz).

## Options that can help

They are under **Settings > Deep scan**. Deep scan, Direct scans and Radio tuning are **Off** by default. All adapters listen is on **Auto** by default, but it only does something once Deep scan is on. They need the [radio helper](deep-scan.md#the-radio-helper) that the install adds, and one restart after the first install.

### Deep scan

A spare USB adapter stops normal scanning and listens to all Wi-Fi traffic instead. Every network it hears goes into the log like any other. It also hears Flock cameras that only send short "probe" messages and never show a network of their own. It never sends anything. See [Deep scan](deep-scan.md).

### All adapters listen

With two or more USB adapters that can listen, **Auto** keeps the fewest adapters on normal scans that still cover every band, and every other USB adapter listens on its own share of the channels. **Off** keeps only one listening adapter.

### Direct scans

USB adapters start their own scans through the radio helper instead of through NetworkManager (the program that manages Wi-Fi connections). The results are fresher.

### Radio tuning

During a wardrive, power saving goes off on USB adapters, and their transmit power goes up to the limit your country allows, as Linux reports it. Both are put back when the wardrive ends. Power saving can make an adapter doze between scans. The install also keeps some devices from going to sleep while they are plugged in: the 7 adapters that can listen, u-blox GPS receivers and nodes on the ESP32-C5's own USB port. See [the radio helper](deep-scan.md#the-radio-helper).

## Legal limits

- Quartermaster **never changes the regulatory domain**, the country setting Linux uses to decide which channels and how much power are allowed. Radio tuning only goes up to the limit that Linux reports for that country. **Settings > Country** sets the channels the nodes use, the LoRa cap's band and the channels Deep scan listens on (only those the adapter allows). It never changes the regulatory domain.
- Deep scan only listens and never sends a frame.
- 6 GHz in the United States is listen-only, and Linux enforces that.
- Quartermaster never joins a network and never pairs with a device.
- Record only what is legal where you are.

## An honest note

Deep scan, All adapters listen, Direct scans, Radio tuning and Power guard are designed and tested not to cost you networks:

- An adapter only listens when every one of its bands is still covered by normal scans (with a single listening adapter, nodes count for 2.4 and 5 GHz). 6 GHz is always kept by an adapter that scans normally.
- The only 6 GHz adapter always keeps scanning normally.
- If the processor gets too busy, listening adapters go back to normal scans one at a time.
- When power is fine, Power guard changes nothing.

Still, every street and every set of adapters is different. Whether an option finds **more** networks for you depends on your route and hardware. The only way to know is to compare.

## How to compare two drives

1. Pick a fixed route of 10 to 20 minutes and drive it four times, at about the same time of day.
2. Use the order **A, B, B, A**: lap 1 with the option off, laps 2 and 3 with it on, lap 4 off again. This evens out changes in traffic and time.
3. Start and stop a separate wardrive for each lap, so each lap has its own log.
4. Compare the number of Wi-Fi networks on each summary, band by band (2.4 / 5 / 6 GHz).

If you have a computer with Python, the repository has a tool that does the comparison for you. Copy the four `.wigle.csv` logs to the computer (see [Files and logs](files.md)) and run, in a copy of the repository:

```sh
python3 tools/deepscan_ab.py --a lap1.wigle.csv lap4.wigle.csv --b lap2.wigle.csv lap3.wigle.csv
```

For each band it prints how many unique networks each set found, how fast new ones came in, and how many of A's networks B found too. It passes only when B finds at least as many networks as A on every band.

## On a DIY Raspberry Pi

- **The built-in Wi-Fi** of a 3B+ or 4B sweeps 2.4 and 5 GHz. On a Zero W, Zero 2 W or 3B it sweeps 2.4 GHz only. See [Wi-Fi adapters](wifi-adapters.md#on-a-diy-raspberry-pi).
- **Set the Pi's own Wi-Fi country.** Quartermaster never changes it, on a Pi either. A ready SD card image does not set one. Until you set it, Wi-Fi keeps to the channels that are allowed everywhere, which leaves out 6 GHz and a few other channels. Set it on the **Localisation** page of Raspberry Pi Imager, or log in and run `sudo raspi-config nonint do_wifi_country US` with your own two-letter country code in place of `US`.
- **Settings > Country** sets the channels the nodes use and the channels Deep scan listens on, as on the CardputerZero. A Pi has no LoRa band to set.
- **The options that can help** need the radio helper. The Pi package and the ready images have it. See [Deep scan](deep-scan.md#on-a-diy-raspberry-pi).
- **To compare two drives,** copy the logs off the Pi as [Files and logs](files.md#on-a-diy-raspberry-pi) shows.
