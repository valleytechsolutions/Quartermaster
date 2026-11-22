"""The 5 V supplies of the cap connector and the Grove port.

The CardputerZero leaves both off, so a GPS on the Cap LoRa-1262 or on the Grove port stays
silent until they are switched on. Whatever this app switches on, it switches off again.
"""

from pathlib import Path
import threading

RAILS = (Path("/sys/class/leds/ext_5v_out/brightness"), Path("/sys/class/leds/grove_5v_out/brightness"))


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
