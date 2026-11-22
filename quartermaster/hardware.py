"""Find what is plugged in: Wi-Fi adapters (built-in and USB) and GPS receivers.

Everything here reads sysfs and `iw`, so it works for any adapter Linux has a
driver for. The chipset table only adds a friendly name; unknown adapters still
work and show their USB product string.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess

SYS_NET = Path("/sys/class/net")
SYS_TTY = Path("/sys/class/tty")

# Driver name -> chipset family, for the adapter line in the UI.
DRIVER_CHIPSETS = {
    "mt7921u": "MediaTek MT7921AU", "mt7921e": "MediaTek MT7921", "mt7925u": "MediaTek MT7925",
    "mt7925e": "MediaTek MT7925", "mt76x2u": "MediaTek MT7612U", "mt76x0u": "MediaTek MT7610U",
    "mt7601u": "MediaTek MT7601U", "rt2800usb": "Ralink RT2800", "ath9k_htc": "Atheros AR9271",
    "rtl8xxxu": "Realtek RTL8xxxU", "rtl88xxau": "Realtek RTL8812AU", "8812au": "Realtek RTL8812AU",
    "rtl8812au": "Realtek RTL8812AU", "88XXau": "Realtek RTL8812AU", "rtl8814au": "Realtek RTL8814AU",
    "8814au": "Realtek RTL8814AU", "rtw_8812au": "Realtek RTL8812AU", "rtw_8821au": "Realtek RTL8821AU",
    "rtw_8814au": "Realtek RTL8814AU", "rtw_8822bu": "Realtek RTL8822BU", "rtw_8821cu": "Realtek RTL8821CU",
    "rtl8852au": "Realtek RTL8852AU", "rtl8852bu": "Realtek RTL8852BU", "rtw89_8852au": "Realtek RTL8852AU",
    "rtw89_8852bu": "Realtek RTL8852BU", "rtl8832au": "Realtek RTL8832AU", "brcmfmac": "Broadcom (built-in)",
    "carl9170": "Atheros AR9170", "rtl8187": "Realtek RTL8187", "rtl8192cu": "Realtek RTL8192CU",
}

# USB vendor:product -> retail name, for adapters wardrivers actually carry.
USB_ADAPTERS = {
    "0e8d:7961": "MT7921AU tri-band (ALFA AWUS036AXML / AXM)",
    "0e8d:7925": "MT7925 tri-band Wi-Fi 7",
    "3574:6211": "Netgear A8000 (MT7921AU tri-band)",
    "0846:9060": "Netgear A8000 (MT7921AU tri-band)",
    "35bc:0107": "TP-Link TXE70UH (MT7921AU tri-band)",
    "0e8d:7612": "ALFA AWUS036ACM (MT7612U dual-band)",
    "0e8d:7610": "MT7610U dual-band (ALFA AWUS036ACHM)",
    "0cf3:9271": "AR9271 2.4 GHz (ALFA AWUS036NHA)",
    "148f:3070": "RT3070 2.4 GHz (ALFA AWUS036NH)",
    "148f:5370": "RT5370 2.4 GHz",
    "148f:5572": "RT5572 dual-band (Panda PAU09)",
    "148f:7601": "MT7601U 2.4 GHz",
    "0bda:8812": "RTL8812AU dual-band (ALFA AWUS036ACH)",
    "0bda:8813": "RTL8814AU dual-band (ALFA AWUS1900)",
    "0bda:0811": "RTL8811AU dual-band (ALFA AWUS036ACS)",
    "0bda:0120": "RTL8811AU dual-band",
    "0bda:b812": "RTL8812BU dual-band",
    "0bda:c811": "RTL8811CU dual-band",
    "0bda:8832": "RTL8832AU Wi-Fi 6 (ALFA AWUS036AX)",
    "0bda:885a": "RTL8852AU Wi-Fi 6",
    "0bda:8187": "RTL8187 2.4 GHz (ALFA AWUS036H)",
}

# USB vendor:product of GPS receivers and the USB-serial bridges they ship on.
USB_GPS = {
    "1546": "u-blox GPS", "2c7c": "Quectel GPS", "067b:2303": "Prolific USB GPS", "067b:23a3": "Prolific USB GPS",
    "1a86:7523": "CH340 USB GPS", "1a86:55d4": "CH9102 USB GPS", "1a86:55d3": "CH343 USB GPS",
    "10c4:ea60": "CP210x USB GPS", "0403:6001": "FTDI USB GPS", "0403:6015": "FTDI USB GPS",
    "1ffb:0092": "LC29H USB GPS",
}


def _read(path):
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return ""


def _usb_parent(device):
    """Walk up from an interface's device node to the USB device that owns it."""
    try:
        node = Path(device).resolve()
    except OSError:
        return None
    while node != node.parent:
        if (node / "idVendor").is_file() and (node / "idProduct").is_file():
            return node
        node = node.parent
    return None


def parse_iw_phy(text):
    """Bands and modes from `iw phy <phy> info`."""
    bands, modes, section, band = set(), set(), None, None
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"Band (\d+):", stripped)
        if match:
            section, band = "band", int(match.group(1))
            continue
        if stripped.startswith("Supported interface modes:"):
            section = "modes"
            continue
        if section == "modes":
            if stripped.startswith("* "):
                modes.add(stripped[2:].strip())
                continue
            section = None
        if section == "band":
            match = re.match(r"\* (\d+)(?:\.\d+)? MHz \[\d+\](.*)", stripped)
            if match and "disabled" not in match.group(2):
                freq = int(match.group(1))
                if freq < 3000:
                    bands.add("2.4")
                elif freq < 5925:
                    bands.add("5")
                elif freq <= 7125:
                    bands.add("6")
    order = [b for b in ("2.4", "5", "6") if b in bands]
    return {"bands": order, "monitor": "monitor" in modes}


def band_label(bands):
    return {1: "single-band", 2: "dual-band", 3: "tri-band"}.get(len(bands), "no bands")


def wifi_adapters(sys_net=SYS_NET, runner=subprocess.run):
    """Every Wi-Fi interface with what the scanner and the UI need to know about it."""
    adapters = []
    try:
        names = sorted(p.name for p in Path(sys_net).iterdir() if (p / "phy80211").exists() or (p / "wireless").exists())
    except OSError:
        names = []
    for name in names:
        base = Path(sys_net) / name
        phy = _read(base / "phy80211" / "name")
        try:
            driver = (base / "device" / "driver").resolve().name
        except OSError:
            driver = ""
        usb = _usb_parent(base / "device")
        usb_id = product = ""
        if usb is not None:
            usb_id = "%s:%s" % (_read(usb / "idVendor").lower(), _read(usb / "idProduct").lower())
            product = " ".join(x for x in (_read(usb / "manufacturer"), _read(usb / "product")) if x)
        info = {"bands": [], "monitor": False}
        if phy and shutil.which("iw"):
            try:
                result = runner(["iw", "phy", phy, "info"], capture_output=True, text=True, timeout=5, check=False)
                if result.returncode == 0:
                    info = parse_iw_phy(result.stdout)
            except (OSError, subprocess.SubprocessError):
                pass
        label = USB_ADAPTERS.get(usb_id) or DRIVER_CHIPSETS.get(driver) or product or driver or "Wi-Fi adapter"
        adapters.append({
            "interface": name, "phy": phy, "driver": driver, "external": usb is not None, "usb_id": usb_id,
            "product": product, "chipset": DRIVER_CHIPSETS.get(driver, ""), "label": label,
            "bands": info["bands"], "band_label": band_label(info["bands"]), "monitor": info["monitor"],
            "mac": _read(base / "address").upper(), "type": _read(base / "type"),
        })
    # External adapters first: they are why the user plugged them in.
    adapters.sort(key=lambda a: (not a["external"], -len(a["bands"]), a["interface"]))
    return adapters


def gps_candidates(sys_tty=SYS_TTY, dev=Path("/dev"), extra=()):
    """Serial ports that may carry NMEA, most likely first: USB receivers, then the cap and Grove UARTs."""
    found = []
    try:
        ttys = sorted(p.name for p in Path(sys_tty).iterdir() if re.fullmatch(r"tty(ACM|USB)\d+", p.name))
    except OSError:
        ttys = []
    for name in ttys:
        usb = _usb_parent(Path(sys_tty) / name / "device")
        usb_id = product = ""
        if usb is not None:
            usb_id = "%s:%s" % (_read(usb / "idVendor").lower(), _read(usb / "idProduct").lower())
            product = " ".join(x for x in (_read(usb / "manufacturer"), _read(usb / "product")) if x)
        known = USB_GPS.get(usb_id) or USB_GPS.get(usb_id.split(":")[0])
        looks_gps = bool(known) or bool(re.search(r"gps|gnss|u-blox|quectel|lc29|nmea", product, re.I))
        found.append({"path": str(Path(dev) / name), "kind": "usb", "usb_id": usb_id,
                      "label": known or product or "USB serial", "likely": looks_gps})
    found.sort(key=lambda item: not item["likely"])
    # The Cap LoRa-1262's GPS and a Grove GPS unit both arrive on the Pi's UARTs.
    for path, label in (("/dev/ttyS0", "Cap / Grove UART"), ("/dev/ttyAMA0", "Grove UART"), ("/dev/serial0", "Pi UART")):
        node = Path(dev) / Path(path).name
        # /dev/serial0 is usually a link to one of the others: list each real port once.
        real = str(node.resolve()) if node.is_symlink() else path
        if node.exists() and all(c["path"] not in (path, real) for c in found):
            found.append({"path": path, "kind": "uart", "usb_id": "", "label": label, "likely": True})
    for path in extra:
        if path and all(c["path"] != path for c in found):
            found.insert(0, {"path": path, "kind": "custom", "usb_id": "", "label": "Custom port", "likely": True})
    return found


WIFI_PRODUCT = re.compile(r"802\.11|wlan|wi-?fi|wireless (lan|adapter|network)", re.I)


def stalled_usb_adapters(sys_usb=Path("/sys/bus/usb/devices")):
    """USB Wi-Fi adapters that are plugged in but have no network interface.

    That is what a driver that failed to start looks like: the adapter is on the bus,
    powered and recognised, yet Linux never created wlanN for it."""
    stalled = []
    try:
        nodes = sorted(Path(sys_usb).iterdir())
    except OSError:
        return stalled
    for node in nodes:
        if not (node / "idVendor").is_file():
            continue
        usb_id = "%s:%s" % (_read(node / "idVendor").lower(), _read(node / "idProduct").lower())
        product = " ".join(x for x in (_read(node / "manufacturer"), _read(node / "product")) if x)
        if usb_id not in USB_ADAPTERS and not WIFI_PRODUCT.search(product):
            continue
        if any(node.glob("*/net/*")) or any(node.glob("*/ieee80211/*")):
            continue
        stalled.append({"usb_id": usb_id, "label": USB_ADAPTERS.get(usb_id) or product or "USB Wi-Fi adapter",
                        "port": node.name})
    return stalled


def local_addresses(runner=subprocess.run):
    """IPv4 addresses other devices on the network can reach, Wi-Fi first."""
    found = []
    try:
        result = runner(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True,
                        timeout=5, check=False)
        for line in result.stdout.splitlines() if result.returncode == 0 else ():
            match = re.match(r"\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/", line)
            if match and not match.group(1).startswith(("docker", "veth", "br-", "tun", "tailscale")):
                found.append((match.group(1), match.group(2)))
    except (OSError, subprocess.SubprocessError):
        pass
    if not found:
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))
                found.append(("", probe.getsockname()[0]))
        except OSError:
            pass
    found.sort(key=lambda item: not item[0].startswith("wl"))
    return [address for _, address in found if not address.startswith("127.")]


def bluetooth_adapters(sys_bt=Path("/sys/class/bluetooth")):
    try:
        names = sorted(p.name for p in Path(sys_bt).iterdir() if re.fullmatch(r"hci\d+", p.name))
    except OSError:
        return []
    out = []
    for name in names:
        usb = _usb_parent(Path(sys_bt) / name / "device")
        out.append({"name": name, "external": usb is not None,
                    "label": (" ".join(x for x in (_read(usb / "manufacturer"), _read(usb / "product")) if x)
                              if usb is not None else "Built-in Bluetooth")})
    return out


def os_release():
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("VERSION_ID="):
            return line.split("=", 1)[1].strip('"')
    return os.uname().release
