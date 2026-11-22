import os
import string

import pytest

from quartermaster import accounts
from quartermaster.ui import keys, recon_screens, screens
from quartermaster.ui.keys import EvdevInput, Key, navigation
from tests.test_ui import make_app, press, screen_text, settle, typed

# The Sym layer of the built-in keyboard as its device tree lists it, row by row.
SYM_LAYER = (
    ("1234567890", (26, 27, 39, 40, 41, 43, 51, 52, 53, 94), "!@#$%^&*()"),
    ("qwertyuiop", (55, 69, 70, 71, 72, 73, 74, 75, 76, 77), "~`_-+=[]{}"),
    ("asdghjkl", (79, 80, 81, 82, 83, 85, 86, 89), ";:'\"<>\\|"),
    ("vbnm", (90, 91, 92, 93), ",./?"),
)
LETTER = {30: "a", 48: "b", 46: "c", 32: "d", 18: "e", 33: "f", 34: "g", 35: "h", 23: "i", 36: "j", 37: "k",
          38: "l", 50: "m", 49: "n", 24: "o", 25: "p", 16: "q", 19: "r", 31: "s", 20: "t", 22: "u", 47: "v",
          17: "w", 45: "x", 21: "y", 44: "z"}
CODE = {letter: code for code, letter in LETTER.items()}
CODE.update({digit: 2 + i for i, digit in enumerate("1234567890")})
SYM = {symbol: code for _, codes, symbols in SYM_LAYER for code, symbol in zip(codes, symbols)}
ESC, BACKSPACE, TAB, ENTER, SHIFT, KEY_CD, KEY_DVD = 1, 14, 15, 28, 42, 383, 389
EV_MSC, MSC_SCAN = 4, 4


def typed_as(board, codes, built_in):
    return "".join(board.translate(code, 1, built_in).char for code in codes)


def test_sym_types_the_symbol_printed_on_every_key_of_the_built_in_keyboard():
    board = EvdevInput(paths=["/nonexistent"])
    for _, codes, symbols in SYM_LAYER:
        assert typed_as(board, codes, True) == symbols
    assert sorted(SYM) == sorted(string.punctuation)
    key = board.translate(70, 1, True)
    assert (key.name, key.char, key.repeat) == ("_", "_", False)


def test_older_system_images_send_other_codes_for_the_same_symbols():
    board = EvdevInput(paths=["/nonexistent"])
    old = list(range(183, 203)) + list(range(209, 218)) + [231, 232, 233]
    assert typed_as(board, old, True) == "!@#$%^&*()~`+-/\\{}[]=:;_?<>'\",.|"
    assert sorted(typed_as(board, old, True)) == sorted(string.punctuation)


def test_the_keymap_file_installed_with_the_driver_corrects_the_table(tmp_path):
    keymap = tmp_path / "keymap.map"
    keymap.write_text("# -- Row 1 --\nkeycode 70 = minus    # was underscore\n  keycode\t300 =underscore\n"
                      "keycode 301 = Hyper_L\nkeycode 30 = minus\nkeycode 28 = minus\n# keycode 26 = at\n"
                      "keycode 4000 = minus\nkeycode = minus\n", encoding="utf-8")
    symbols = keys.built_in_symbols(keymap)
    assert (symbols[70], symbols[300], symbols[26], symbols[71], symbols[212]) == ("-", "_", "!", "-", "_")
    assert 301 not in symbols and 30 not in symbols and 400 not in symbols and 4000 not in symbols
    assert keys.built_in_symbols(tmp_path / "missing.map") == keys.BUILT_IN_SYMBOLS
    keymap.write_bytes(b"\xff\xfe keycode 70 = plus\nkeycode 71 = \xff\n")
    assert keys.built_in_symbols(keymap) == keys.BUILT_IN_SYMBOLS
    board = EvdevInput(paths=["/nonexistent"])
    board.symbols = symbols
    assert typed_as(board, (70, 300, 30), True) == "-_a" and board.translate(28, 1, True) == "ENTER"
    assert sorted(keys.KEYSYMS.values()) == sorted(string.punctuation)


def test_other_keyboards_are_read_as_a_us_layout():
    board = EvdevInput(paths=["/nonexistent"])
    assert typed_as(board, (12, 13, 26, 27, 39, 40, 41, 43, 51, 52, 53), False) == "-=[];'`\\,./"
    board.translate(SHIFT, 1)
    assert typed_as(board, (12, 13, 26, 27, 39, 40, 41, 43, 51, 52, 53, 2, 30), False) == "_+{}:\"~|<>?!A"
    board.translate(SHIFT, 0)
    assert typed_as(board, (2, 30), False) == "1a"
    assert [board.translate(code, 1) for code in (70, 71, 94, 183, 212)] == [None] * 5


def test_a_shift_key_held_down_stays_shift_while_it_repeats():
    board = EvdevInput(paths=["/nonexistent"])
    assert board.translate(SHIFT, 1) is None and board.translate(SHIFT, 2) is None
    assert typed_as(board, (30, 2), False) == "A!"
    assert board.translate(SHIFT, 0) is None and typed_as(board, (30, 2), False) == "a1"


def test_caps_lock_switches_letters_only():
    board = EvdevInput(paths=["/nonexistent"])
    assert board.translate(58, 1) is None and board.translate(58, 0) is None
    assert typed_as(board, (30, 2, 12), False) == "A1-"
    board.translate(SHIFT, 1)
    assert typed_as(board, (30, 2, 12), False) == "a!_"
    board.translate(SHIFT, 0)
    board.translate(58, 2)
    assert typed_as(board, (30,), False) == "A"
    board.translate(58, 1)
    assert typed_as(board, (30,), False) == "a"


def bits(*numbers):
    """What the keyboard answers when asked which keys are down or which lights are lit."""
    return sum(1 << number for number in numbers).to_bytes(96, "little")


def test_a_shift_or_caps_lock_that_was_on_before_the_start_is_seen(monkeypatch):
    answers = {keys.KEYS_DOWN: bits(SHIFT), keys.LIGHTS_ON: bits()}
    monkeypatch.setattr(keys.fcntl, "ioctl", lambda fd, request, buffer: answers[request])
    board = EvdevInput(paths=["/dev/null"])
    assert board.shift and not board.caps and typed_as(board, (30,), False) == "A"
    board.translate(SHIFT, 0)
    assert typed_as(board, (30,), False) == "a"
    board.close()
    answers.update({keys.KEYS_DOWN: bits(30, 54), keys.LIGHTS_ON: bits(0, keys.CAPS_LIGHT)})
    board = EvdevInput(paths=["/nonexistent", "/dev/null"])
    assert board.shift and board.caps and typed_as(board, (30, 2), False) == "a!"
    board.close()
    monkeypatch.setattr(keys, "is_built_in", lambda path: True)
    monkeypatch.setattr(keys, "built_in_symbols", lambda: {54: ")"})
    board = EvdevInput(paths=["/dev/null"])
    assert not board.shift and board.caps
    board.close()
    answers.update({keys.KEYS_DOWN: bits(SHIFT, 54), keys.LIGHTS_ON: bits(0, 2)})
    board = EvdevInput(paths=["/dev/null"])
    assert board.shift and not board.caps
    board.close()
    monkeypatch.undo()
    board = EvdevInput(paths=["/dev/null"])
    assert not board.shift and not board.caps and keys.state(next(iter(board.devices)), keys.KEYS_DOWN) == 0
    board.close()


def test_an_unplugged_keyboard_takes_its_shift_and_caps_lock_along(monkeypatch):
    board = EvdevInput(paths=["/dev/null", "/dev/null"])
    usb, other = list(board.devices)
    answers = {usb: {keys.KEYS_DOWN: bits(54), keys.LIGHTS_ON: bits(keys.CAPS_LIGHT)}}
    monkeypatch.setattr(keys.fcntl, "ioctl", lambda fd, request, buffer: answers.get(fd, {}).get(request, bits()))
    board.translate(58, 1)
    board.translate(54, 1)
    assert board.caps and board.shift and typed_as(board, (30, 2), False) == "a!"
    board._drop(usb)
    assert not board.caps and not board.shift and typed_as(board, (30, 2), False) == "a1"
    assert list(board.devices) == [other]
    answers.clear()
    answers[other] = {keys.KEYS_DOWN: bits(SHIFT, 30)}
    board.look()
    assert board.shift and not board.caps
    board.close()
    assert board.devices == {} and not board.shift


def test_shift_or_caps_lock_on_any_one_keyboard_counts(monkeypatch):
    def asked(number):
        return 2 << 30 | 96 << 16 | ord("E") << 8 | number
    assert (keys.KEYS_DOWN, keys.LIGHTS_ON, keys.CAPS_LIGHT) == (asked(0x18), asked(0x19), 1)
    board = EvdevInput(paths=["/dev/null", "/dev/null"])
    first, second = list(board.devices)
    answers = {}

    def ioctl(fd, request, buffer):
        assert len(buffer) == 96
        return answers.get((fd, request), bits())
    monkeypatch.setattr(keys.fcntl, "ioctl", ioctl)
    for holder in (first, second):
        answers.clear()
        answers[holder, keys.KEYS_DOWN] = bits(SHIFT)
        board.look()
        assert board.shift and not board.caps
        answers.clear()
        answers[holder, keys.LIGHTS_ON] = bits(keys.CAPS_LIGHT)
        board.look()
        assert board.caps and not board.shift
    board.close()


def test_modifier_and_media_keys_of_the_built_in_keyboard_type_nothing():
    board = EvdevInput(paths=["/nonexistent"])
    for code in (KEY_CD, KEY_DVD, 29, 56, 59, 68, 87, 99, 113, 114, 115, 138, 163, 164, 165, 224, 225, 0, 78):
        assert board.translate(code, 1, True) is None and board.translate(code, 0, True) is None
    assert [board.translate(code, 1, True).name for code in (103, 108, 105, 106, 111, 102, 107, 104, 109)] == \
        ["UP", "DOWN", "LEFT", "RIGHT", "DELETE", "HOME", "END", "PAGEUP", "PAGEDOWN"]
    assert board.translate(30, 2, True).repeat and board.translate(30, 0, True) is None


def test_f_x_z_c_are_arrows_only_as_navigation():
    assert [navigation(Key(ch.upper(), ch)).name for ch in "fxzcFXZC"] == ["UP", "DOWN", "LEFT", "RIGHT"] * 2
    assert navigation(Key("UP", "f")).char == "" and navigation(Key("F", "f", repeat=True)).repeat
    for key in (Key("W", "w"), Key("ENTER"), Key(";", ";"), Key("`", "`"), Key(".", "."), Key("SPACE", " ")):
        assert navigation(key) is key


def test_the_built_in_keyboard_is_recognised(tmp_path, monkeypatch):
    node = tmp_path / "event11"
    node.write_text("")
    (tmp_path / "internal").symlink_to(node)
    monkeypatch.setattr(keys, "INTERNAL", str(tmp_path / "internal"))
    for event, name in (("event3", "tca8418c\n"), ("event5", "tca8418\n"), ("event7", "Logitech USB Keyboard\n")):
        (tmp_path / event / "device").mkdir(parents=True)
        (tmp_path / event / "device" / "name").write_text(name)
    assert keys.is_built_in("/dev/input/event3", tmp_path) and keys.is_built_in("/dev/input/event5", tmp_path)
    assert not keys.is_built_in("/dev/input/event7", tmp_path) and not keys.is_built_in("/dev/input/event9", tmp_path)
    assert keys.is_built_in(str(node), tmp_path)


def test_a_keyboard_is_opened_with_its_kind_and_the_installed_keymap(tmp_path, monkeypatch):
    node, keymap = tmp_path / "event11", tmp_path / "keymap.map"
    node.write_text("")
    keymap.write_text("keycode 300 = underscore\n")
    (tmp_path / "internal").symlink_to(node)
    monkeypatch.setattr(keys, "INTERNAL", str(tmp_path / "internal"))
    monkeypatch.setattr(keys, "KEYMAP", str(keymap))
    board = EvdevInput(paths=[str(node), "/nonexistent", "/dev/null"])
    assert list(board.devices.values()) == [True, False] and board.symbols[300] == "_" and board.symbols[70] == "_"
    board.close()
    monkeypatch.setattr(keys, "find_keyboards", lambda: ["/dev/null", str(node)])
    board = EvdevInput()
    assert list(board.devices.values()) == [False, True]
    board.close()


def event(kind, code, value):
    return keys.EVENT.pack(0, 0, kind, code, value)


@pytest.fixture
def device(tmp_path):
    """An app whose keyboard is a pipe carrying raw input events, as the built-in keyboard sends them.

    Each key comes with its scan code before it and a report mark after it.
    """
    app = make_app(tmp_path)
    read, write = os.pipe()
    os.set_blocking(read, False)
    app.keyboard = EvdevInput(paths=["/nonexistent"])
    app.keyboard.devices[read] = True

    def send(*strokes):
        for stroke in strokes:
            os.write(write, b"".join(event(EV_MSC, MSC_SCAN, code) + event(keys.EV_KEY, code, value) + event(0, 0, 0)
                                     for code, value in stroke))
        for _ in range(len(strokes) + 2):
            app.step(0)
    app.send = send
    app.keyboard_write = write
    yield app
    app.keyboard.close()
    os.close(write)


def tap(code):
    return [(code, 1), (code, 0)]


def sym(symbol):
    """Sym tapped once, then a key: the driver wraps the symbol's own code in KEY_CD."""
    return [(KEY_CD, 1), (SYM[symbol], 1), (SYM[symbol], 0), (KEY_CD, 0)]


def shifted(letter):
    return [(SHIFT, 1), (CODE[letter], 1), (CODE[letter], 0), (SHIFT, 0)]


def test_a_key_with_dashes_and_underscores_is_typed_on_the_built_in_keyboard(device, tmp_path, drawn):
    app = device
    app.send(tap(CODE["k"]), *[tap(CODE["x"])] * 4, tap(ENTER))
    assert isinstance(app.screen, screens.TextInput) and app.screen.title == "Wardrift API key"
    strokes = [tap(CODE[ch]) if ch in CODE else sym(ch) for ch in "wdk_live-8f3c"]
    app.send(*strokes, shifted("z"), sym("."), sym("%"), tap(CODE["x"]), tap(BACKSPACE))
    assert app.screen.value == "wdk_live-8f3cZ.%"
    assert "wdk_live-8f3cZ.%" in screen_text(app, drawn)
    app.send(tap(ENTER))
    assert accounts.load(tmp_path / "accounts.json")["wardrift_key"] == "wdk_live-8f3cZ.%"
    settle(app)
    assert isinstance(app.screen, screens.Keys) and app.tests["wardrift"][1] == "Signed in to wardrift"


def hold(code, repeats=3):
    return [(code, 1)] + [(code, 2)] * repeats + [(code, 0)]


def test_a_held_key_repeats_only_where_repeating_helps(device, tmp_path):
    app = device
    app.keyboard.devices = dict.fromkeys(app.keyboard.devices, False)
    app.send(hold(CODE["k"]), *[tap(108)] * 4, hold(ENTER))
    assert isinstance(app.screen, screens.TextInput) and app.screen.title == "Wardrift API key"
    app.send(hold(CODE["w"], 2), tap(TAB), hold(ENTER))
    assert app.screen.value == "www-" and app.screen.text_mode
    app.send(hold(BACKSPACE, 2), hold(TAB))
    assert app.screen.value == "w" and not app.screen.text_mode
    app.send(hold(106, 2), hold(CODE["c"], 1), hold(ESC))
    assert app.screen.cell == 5 and app.screen.text_mode and app.screen.value == "w"
    app.send(hold(ESC))
    assert isinstance(app.screen, screens.Keys) and accounts.load(tmp_path / "accounts.json")["wardrift_key"] == ""
    app.send(hold(103, 2), hold(ENTER))
    assert isinstance(app.screen, screens.TextInput) and app.screen.title != "Wardrift API key"
    app.send(hold(ESC), hold(ESC), hold(CODE["w"]))
    assert app.wardriving and isinstance(app.screen, screens.Wardrive)
    app.send(hold(57), hold(ENTER))
    assert isinstance(app.screen, screens.Confirm) and app.wardriving


def test_a_held_arrow_changes_a_choice_once_and_does_not_run_on(device):
    app = device
    app.keyboard.devices = dict.fromkeys(app.keyboard.devices, False)
    app.send(tap(CODE["s"]), hold(106, 2))
    assert isinstance(app.screen, screens.Settings) and app.cfg["wifi_adapters"] == "external"
    app.send(hold(105, 1))
    assert app.cfg["wifi_adapters"] == "all"
    app.send(hold(106, 2))
    app.send(tap(ESC), tap(CODE["w"]), hold(106, 4), hold(105, 3))
    assert app.wardriving and app.screen.filter == "ble"
    for arrow, repeats in ((105, 1), (106, 2), (103, 3), (108, 1)):
        app.send(tap(ENTER), hold(arrow, repeats))
        assert isinstance(app.screen, screens.Confirm) and app.screen.choice == 0
        app.send(tap(ENTER))
        assert isinstance(app.screen, screens.Wardrive) and app.wardriving


def test_a_held_key_stays_with_the_screen_it_was_pressed_on(device):
    app = device
    app.keyboard.devices = dict.fromkeys(app.keyboard.devices, False)
    app.send(tap(CODE["a"]), tap(CODE["t"]))
    assert isinstance(app.screen, screens.Tips)
    app.send(hold(BACKSPACE, 4))
    assert isinstance(app.screen, screens.About)
    app.send(hold(BACKSPACE, 4))
    assert isinstance(app.screen, screens.Home)
    app.push(screens.IgnoreList())
    app.send(hold(CODE["a"], 4))
    assert isinstance(app.screen, screens.TextInput) and app.screen.value == ""
    app.send(hold(CODE["a"], 4), hold(BACKSPACE, 1), hold(111, 1), hold(ENTER, 3))
    assert isinstance(app.screen, screens.IgnoreList) and app.cfg["ignore"] == ["a"]


def test_a_held_key_moves_by_exactly_as_many_rows_as_it_repeats(device):
    app = device
    app.keyboard.devices = dict.fromkeys(app.keyboard.devices, False)
    app.send(tap(CODE["k"]), hold(108, 2))
    assert isinstance(app.screen, screens.Keys) and app.screen.index == 3
    app.send(hold(103, 1), hold(CODE["x"], 1))
    assert app.screen.index == 3
    app.send(tap(ESC), tap(CODE["a"]), tap(CODE["t"]))
    app.send(hold(109, 2))
    assert isinstance(app.screen, screens.Tips) and app.screen.top == 3 * app.screen.VISIBLE
    app.send(hold(104, 1))
    assert app.screen.top == app.screen.VISIBLE
    app.send(tap(ESC), tap(ESC), tap(CODE["r"]), hold(57, 3))
    assert isinstance(app.screen, recon_screens.Recon) and app.recon_running


def test_only_the_key_pressed_last_repeats(device):
    app = device
    app.keyboard.devices = dict.fromkeys(app.keyboard.devices, False)
    app.send([(106, 2)] * 3)
    assert isinstance(app.screen, screens.Home) and app.screen.index == 0
    app.send(hold(106, 2), hold(105, 1))
    assert app.screen.index == 1
    app.send(tap(CODE["k"]), [(108, 1)], tap(103), [(108, 2)] * 3)
    assert isinstance(app.screen, screens.Keys) and app.screen.index == 0
    app.send([(108, 0)], hold(108, 2))
    assert app.screen.index == 3


def test_an_early_driver_sent_the_right_shift_code_for_a_symbol(tmp_path):
    keymap = tmp_path / "keymap.map"
    keymap.write_text("keycode 54 = parenright\nkeycode 42 = minus\n")
    board = EvdevInput(paths=["/nonexistent"])
    board.symbols = keys.built_in_symbols(keymap)
    assert typed_as(board, (54, 30), True) == ")a" and board.translate(54, 0, True) is None
    assert board.translate(54, 1, False) is None and typed_as(board, (30,), False) == "A"
    board.translate(54, 0, False)
    assert 42 not in board.symbols and board.translate(SHIFT, 1, True) is None and typed_as(board, (30,), True) == "A"


def test_keys_pressed_faster_than_the_screen_is_read_keep_their_meaning(device, tmp_path):
    app = device
    app.send(tap(CODE["k"]), *[tap(CODE["x"])] * 4, tap(ENTER), tap(CODE["f"]), tap(CODE["x"]), sym("_"),
             tap(TAB), tap(CODE["c"]), tap(ENTER), tap(CODE["c"]), tap(ENTER))
    assert accounts.load(tmp_path / "accounts.json")["wardrift_key"] == "fx__c"
    assert isinstance(app.screen, screens.Keys)


def test_every_symbol_can_be_picked_from_a_list(tmp_path, drawn):
    app = make_app(tmp_path)
    press(app, "K", "DOWN", "DOWN", "DOWN", "DOWN", "ENTER")
    box = app.screen
    assert sorted(box.SYMBOLS) == sorted(string.punctuation) and len(box.SYMBOLS) == 2 * box.COLUMNS
    assert box.text_mode and ("Tab", "Symbols") in box.hints(app)
    typed(app, "ab")
    press(app, "TAB")
    assert not box.text_mode and ("Tab", "Symbols") not in box.hints(app)
    text = screen_text(app, drawn).split("\n")
    assert all(symbol in text for symbol in string.punctuation) and not any("typed" in line for line in text)
    press(app, "ENTER")
    assert box.value == "ab-" and box.text_mode
    typed(app, "c")
    press(app, "TAB", "C", "ENTER", "TAB", "ENTER", "TAB", "Z", "Z", "ENTER", "TAB", "X", "ENTER", "TAB", "F", "F")
    assert box.value == "ab-c__;~" and box.SYMBOLS[box.cell] == "~" and not box.text_mode
    press(app, "LEFT", "LEFT", "ENTER")
    assert box.value == "ab-c__;~="
    press(app, "TAB", "BACKSPACE", "W", "ESC")
    assert box.value == "ab-c__;~" and box.text_mode and app.screen is box
    box.limit = len(box.value)
    press(app, "TAB", "ENTER")
    typed(app, "z")
    assert box.value == "ab-c__;~" and box.text_mode
    press(app, "ESC")
    assert isinstance(app.screen, screens.Keys) and accounts.load(tmp_path / "accounts.json")["wardrift_key"] == ""


def test_keys_waiting_to_be_read_can_be_forgotten(device):
    os.write(device.keyboard_write, b"".join(event(keys.EV_KEY, code, value) for code, value in tap(30) + tap(48)))
    device.keyboard.flush()
    assert device.keyboard.poll(0) == []
