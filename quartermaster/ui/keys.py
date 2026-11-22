"""Keyboard input from Linux evdev, mapped to logical keys.

F, X, Z and C carry the arrows on the CardputerZero keyboard and act as Up, Down,
Left and Right without Fn, as do ; . , / and ` acts as Escape. Text fields read
the letters themselves; there only ` keeps its meaning, so a field can be left.
"""

import os
from pathlib import Path
import select
import struct

EVENT = struct.Struct("llHHi")
EV_KEY = 1

# linux/input-event-codes.h
CODES = {
    1: "ESC", 14: "BACKSPACE", 15: "TAB", 28: "ENTER", 57: "SPACE", 96: "ENTER",
    103: "UP", 108: "DOWN", 105: "LEFT", 106: "RIGHT", 102: "HOME", 107: "END",
    104: "PAGEUP", 109: "PAGEDOWN", 111: "DELETE",
    42: "SHIFT", 54: "SHIFT", 29: "CTRL", 97: "CTRL", 56: "ALT", 100: "ALT", 464: "FN",
}
CHARS = {
    2: "1!", 3: "2@", 4: "3#", 5: "4$", 6: "5%", 7: "6^", 8: "7&", 9: "8*", 10: "9(", 11: "0)",
    12: "-_", 13: "=+", 16: "qQ", 17: "wW", 18: "eE", 19: "rR", 20: "tT", 21: "yY", 22: "uU",
    23: "iI", 24: "oO", 25: "pP", 26: "[{", 27: "]}", 30: "aA", 31: "sS", 32: "dD", 33: "fF",
    34: "gG", 35: "hH", 36: "jJ", 37: "kK", 38: "lL", 39: ";:", 40: "'\"", 41: "`~", 43: "\\|",
    44: "zZ", 45: "xX", 46: "cC", 47: "vV", 48: "bB", 49: "nN", 50: "mM", 51: ",<", 52: ".>",
    53: "/?",
}
ARROW_ALIASES = {";": "UP", ".": "DOWN", ",": "LEFT", "/": "RIGHT", "`": "ESC",
                 "f": "UP", "x": "DOWN", "z": "LEFT", "c": "RIGHT"}


class Key:
    """A key press: `name` is logical (UP, ENTER, A...), `char` is typed text."""

    __slots__ = ("name", "char", "repeat")

    def __init__(self, name, char="", repeat=False):
        self.name, self.char, self.repeat = name, char, repeat

    def __eq__(self, other):
        return self.name == other if isinstance(other, str) else NotImplemented

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return "Key(%r)" % self.name


def find_keyboards():
    """Input devices whose key bitmap includes letters, from /proc/bus/input/devices."""
    paths = []
    try:
        blocks = Path("/proc/bus/input/devices").read_text().split("\n\n")
    except OSError:
        return paths
    for block in blocks:
        handlers = next((line for line in block.splitlines() if line.startswith("H: Handlers=")), "")
        keybits = next((line for line in block.splitlines() if line.startswith("B: KEY=")), "")
        events = [h for h in handlers.split("=", 1)[-1].split() if h.startswith("event")]
        if not events or not keybits:
            continue
        words = keybits.split("=", 1)[1].split()
        bits = int("".join(w.rjust(16, "0") for w in words), 16) if words else 0
        if bits >> 30 & 1 and bits >> 16 & 1:  # KEY_A and KEY_Q
            paths.append("/dev/input/" + events[0])
    return paths


class EvdevInput:
    def __init__(self, paths=None):
        self.devices = []
        for path in paths or find_keyboards():
            try:
                self.devices.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                continue
        self.shift = self.fn = self.ctrl = False
        self.text_mode = False

    def poll(self, timeout):
        """Return a list of Keys pressed within `timeout` seconds."""
        if not self.devices:
            select.select([], [], [], timeout)
            return []
        ready, _, _ = select.select(self.devices, [], [], timeout)
        keys = []
        for fd in ready:
            try:
                data = os.read(fd, EVENT.size * 32)
            except (BlockingIOError, OSError):
                continue
            for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, kind, code, value = EVENT.unpack_from(data, offset)
                if kind != EV_KEY:
                    continue
                key = self.translate(code, value)
                if key is not None:
                    keys.append(key)
        return keys

    def translate(self, code, value):
        name = CODES.get(code)
        if name in ("SHIFT", "FN", "CTRL"):
            setattr(self, name.lower(), value != 0)
            return None
        if name == "ALT":
            return None
        if value == 0:
            return None
        repeat = value == 2
        if code in CHARS:
            char = CHARS[code][1 if self.shift else 0]
            if (self.fn or not self.shift) and not self.ctrl and char in ARROW_ALIASES and \
                    (not self.text_mode or char == "`"):
                return Key(ARROW_ALIASES[char], "" if self.text_mode else char, repeat)
            return Key(char.upper() if char.isalpha() else char, char, repeat)
        if name:
            return Key(name, " " if name == "SPACE" else "", repeat)
        return None

    def close(self):
        for fd in self.devices:
            try:
                os.close(fd)
            except OSError:
                pass


class ScriptedInput:
    """Feeds a fixed key sequence; used by tests and the screenshot renderer."""

    def __init__(self, names=()):
        self.queue = []
        self.text_mode = False
        self.push(*names)

    def push(self, *names):
        self.queue.extend(Key(n, n if len(n) == 1 else (" " if n == "SPACE" else "")) for n in names)

    def type(self, text):
        self.queue.extend(Key(ch.upper() if ch.isalpha() else ch, ch) for ch in text)

    def poll(self, timeout):
        return [self.queue.pop(0)] if self.queue else []

    def close(self):
        pass
