# Wi-Fi adapters

[Back to the guide](README.md)

## The short version

- The chip inside the adapter matters, not the brand. Look up the chip of a model before you buy it.
- An adapter works when the CardputerZero's Linux (version 6.18) has a driver for its chip. Quartermaster adds no drivers of its own.
- Small "nano" adapters are the safest choice for power. Big adapters with several antennas may need a [powered USB hub](power.md).
- Plug adapters in or out at any time, even during a wardrive. They join or leave within seconds.

## Which chips work

| Chip | Example adapters | Works? |
| --- | --- | --- |
| MediaTek MT7921AU | ALFA AWUS036AXM / AXML, Netgear A8000, COMFAST CF-952AX, HackerGadgets AC1200 USB-C | Yes, 2.4, 5 and 6 GHz |
| MediaTek MT7925U | Netgear A9000 | Yes, 2.4, 5 and 6 GHz |
| MediaTek MT7612U, MT7610U, MT7601U | ALFA AWUS036ACM, Netgear A6210, ALFA AWUS036ACHM | Yes, after installing firmware-misc-nonfree |
| Realtek RTL8812AU, RTL8811AU / 8821AU | ALFA AWUS036ACH, TP-Link Archer T2U, Netgear A6100 | Yes |
| Realtek RTL8814AU | ALFA AWUS1900, Netgear A7000 | Yes, with enough power |
| Realtek RTL8812BU / 8822BU, RTL8822CU | TP-Link Archer T3U / T3U Plus | Yes |
| Realtek RTL8811CU / 8821CU, RTL8723DU | Most "AC600" and "AC650" nano adapters | Yes |
| Realtek RTL8851BU, RTL8852BU | TP-Link Archer TX10UB Nano, TX20U | Yes |
| Realtek RTL8192EU, RTL8188EU, RTL8192CU, RTL8192FU and other N150 / N300 chips | TP-Link TL-WN722N v2/v3, TL-WN822N, Edimax EW-7811Un | Yes, 2.4 GHz |
| Atheros AR9271, AR9170 | ALFA AWUS036NHA | Yes, 2.4 GHz |
| Ralink RT2800 family (RT3070, RT5370, RT5572) | ALFA AWUS036NH, Panda PAU09 | Yes, after installing firmware-misc-nonfree |
| Realtek RTL8852AU / 8832AU | ASUS USB-AX56, ALFA AWUS036AXER | Not yet. Needs Linux 6.19 |
| Realtek RTL8852CU / 8832CU | TP-Link Archer TX50U | Not yet. Needs Linux 6.19 (the TX50U itself Linux 7.0) |
| Realtek RTL8922AU (Wi-Fi 7) | Wi-Fi 7 USB adapters | Not yet. Needs Linux 7.2 |

"Not yet" chips start working when the CardputerZero system moves to a newer Linux. An app cannot install a newer Linux.

Some very new models of a supported chip came out after Linux 6.18. The Hardware screen then says "This model is too new for the driver here."

## 6 GHz

To scan 6 GHz you need a Wi-Fi 6E chip: the **MediaTek MT7921AU** or **MT7925U**. Of the chips that work today, only these two reach 6 GHz. The built-in Wi-Fi stops at 2.4 GHz. The RTL8852CU / 8832CU (Wi-Fi 6E) and RTL8922AU (Wi-Fi 7) chips are made for 6 GHz too, but they do not work yet (see the table).

In the United States, 6 GHz may only be scanned by listening: the adapter waits to hear each network instead of calling out to it. Linux enforces this rule, and Quartermaster leaves it alone.

[Deep scan](deep-scan.md) never takes the only 6 GHz adapter away from normal scans.

## "Driver CD" adapters

Many cheap Realtek adapters first show up as a small disc with a Windows driver on it, not as Wi-Fi. Quartermaster installs **usb-modeswitch**, a small tool that switches most of them to Wi-Fi by themselves after a few seconds.

If one stays a disc, the Hardware screen says "Wi-Fi adapter in setup mode. It shows as a driver disc. Replug it." If replugging does not help, the tool does not know that model yet. For the RTL8192FU disc (USB id `0bda:a192`), run this once in a terminal:

```sh
sudo usb_modeswitch -v 0bda -p a192 -K
```

## Firmware

Most adapters need a small file called firmware to start. The install brings the firmware packages for MediaTek Wi-Fi 6 chips, Realtek and Atheros (firmware-mediatek, firmware-realtek, firmware-atheros).

The **MT7601U, MT7610U, MT7612U** and **Ralink** adapters need **firmware-misc-nonfree**, which the CardputerZero image leaves out. Quartermaster does not install it for you, so that its own install never fails when that package cannot be fetched. Install it once, while online:

```sh
sudo apt install firmware-misc-nonfree
```

Then unplug the adapter and plug it in again. The Hardware screen names the package when one is missing: "Firmware missing. Install firmware-misc-nonfree." If it says "Firmware missing. This system needs newer firmware.", the package is there but too old for this adapter.

## Which adapters can listen (for Deep scan)

[Deep scan](deep-scan.md) needs an adapter whose driver supports monitor mode (listening to all radio traffic). Quartermaster uses it with:

- MediaTek MT7921AU, MT7925U, MT7612U and MT7610U,
- Realtek RTL8812BU / 8822BU and RTL8811CU / 8821CU,
- Atheros AR9271.

Normal wardriving needs no monitor mode. Every adapter marked Yes above scans normally.

## Recommended

- **Wi-Fi:** an MT7921AU adapter, such as the HackerGadgets AC1200 USB-C WiFi Card, for 2.4, 5 and 6 GHz in one.
- **Simple extras:** TP-Link Archer T2U or T3U, TP-Link Archer TX10UB Nano, RTL8811CU nano adapters.

## How many adapters?

See [Power and USB hubs](power.md#how-many-adapters).

## On a DIY Raspberry Pi

**Which Linux.** On a Pi, the Linux of Raspberry Pi OS decides which chips work, not the CardputerZero's Linux 6.18. To see its version, log in to the Pi and type `uname -r`. Some chips in the table above got their driver in a newer Linux than some Pis have: the MT7925U in Linux 6.7, the RTL8812AU and RTL8811AU / 8821AU in 6.13, the RTL8814AU in 6.15, and the RTL8851BU and RTL8852BU in 6.17. When the Pi's Linux has no driver for a chip, the Hardware screen says "No driver for it here. It needs Linux X."

**The built-in Wi-Fi** of a Zero W, Zero 2 W or 3B covers 2.4 GHz. A 3B+ or 4B also covers 5 GHz. None of them covers 6 GHz. For 6 GHz, add an MT7921AU or MT7925U adapter, and set the Pi's own Wi-Fi country. See [Getting the most networks](more-networks.md#on-a-diy-raspberry-pi).

**Firmware and driver discs.** The Pi package brings the same firmware packages and the same **usb-modeswitch** as the CardputerZero package. When an adapter needs **firmware-misc-nonfree** and the Pi does not have it, the Hardware screen names it. Install it as shown above.

**On a Zero,** there is only one USB port for data, so the adapters go on an OTG hub. See [Power and USB hubs](power.md#on-a-diy-raspberry-pi).
