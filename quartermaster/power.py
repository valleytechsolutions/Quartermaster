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
    """What a file says: "" when it is not there, None when it is there and cannot be read."""
    try:
        return path.read_text(errors="replace").strip()
    except FileNotFoundError:
        return ""
    # A read the kernel answers with "try again" reaches Python as a TypeError.
    except (OSError, TypeError):
        return None


def battery(root=SUPPLIES):
    """The device's own battery as {"percent", "charging"}, or None when the system reports none.

    A wireless keyboard or mouse reports a battery of its own, marked with the scope "Device".
    A reading with a part that could not be read is no reading: the fuel gauge misses one now and then.
    """
    try:
        supplies = sorted(Path(root).iterdir())
    except OSError:
        return None
    for supply in supplies:
        if _word(supply / "type") != "Battery" or _word(supply / "scope") == "Device":
            continue
        level, status, present = (_word(supply / name) for name in ("capacity", "status", "present"))
        if None in (level, status, present):
            return None
        if re.fullmatch("[0-9]{1,3}", level) and int(level) <= 100 and present != "0":
            return {"percent": int(level), "charging": status == "Charging"}
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
