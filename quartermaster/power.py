"""The battery level, and the 5 V supplies of the cap connector and the Grove port.

The CardputerZero leaves both supplies off, so a GPS on the Cap LoRa-1262 or on the Grove port
stays silent until they are switched on. Whatever this app switches on, it switches off again.
"""

from pathlib import Path
import re
import threading

RAILS = (Path("/sys/class/leds/ext_5v_out/brightness"), Path("/sys/class/leds/grove_5v_out/brightness"))
SUPPLIES = Path("/sys/class/power_supply")


def _word(path):
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def battery(root=SUPPLIES):
    """The device's own battery as {"percent", "charging"}, or None when the system reports none.

    A wireless keyboard or mouse reports a battery of its own, marked with the scope "Device".
    """
    try:
        supplies = sorted(Path(root).iterdir())
    except OSError:
        return None
    for supply in supplies:
        if _word(supply / "type") != "Battery" or _word(supply / "scope") == "Device":
            continue
        level = _word(supply / "capacity")
        if re.fullmatch("[0-9]{1,3}", level) and int(level) <= 100 and _word(supply / "present") != "0":
            return {"percent": int(level), "charging": _word(supply / "status") == "Charging"}
    return None


class Rails:
    def __init__(self, paths=RAILS):
        self.paths = [Path(path) for path in paths]
        self._original = {}
        self._lock = threading.Lock()

    def on(self):
        """Switch on every supply that is off. True when one was switched on just now."""
        switched = False
        with self._lock:
            for path in self.paths:
                try:
                    value = path.read_text().strip()
                    if value == "0":
                        path.write_text("1")
                        self._original[path] = value
                        switched = True
                except OSError:
                    continue
        return switched

    def restore(self):
        with self._lock:
            for path, value in self._original.items():
                try:
                    path.write_text(value)
                except OSError:
                    pass
            self._original.clear()
