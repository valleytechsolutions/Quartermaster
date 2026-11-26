import sys
import time

import pytest
from PIL import Image, ImageChops, features

from quartermaster.names import one_line
from quartermaster.ui import gfx
from tools import render_screens

FOLDER = render_screens.ROOT / "docs" / "screens" / "diy"
SIDES = {"128": 256, "240": 240}
PLANNED = ("menu", "wardrive", "actions", "stop", "stop-no-save", "alert", "hardware", "settings", "logs", "keys",
           "phone-setup", "keyboard", "aircraft", "aircraft-detail", "aircraft-route", "recon", "about")
AGAIN = "Write the DIY pictures again: python3 tools/render_screens.py --out docs/screens --diy"
MOST, SLIGHTLY, FEW = 64, 24, 32


@pytest.fixture
def utc(monkeypatch):
    """Dates are drawn in UTC, as when the pictures were written."""
    with monkeypatch.context() as patch:
        patch.setenv("TZ", "UTC")
        time.tzset()
        yield
    time.tzset()


def changed(picture, saved):
    """Older Pillow versions smooth the edges of letters a little differently, so a few pixels may be off by a
    little. A changed word or number is off by much more, and a changed color changes many pixels."""
    red, green, blue = ImageChops.difference(picture, saved.convert("RGB")).split()
    off = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
    return any(off[MOST + 1:]) or sum(off[SLIGHTLY + 1:]) > FEW


def test_every_diy_picture_is_there_for_both_hats():
    """The 1.44 inch HAT's pictures are at twice its size, the 1.3 inch HAT's at its own size."""
    assert set(PLANNED) <= set(render_screens.DIY)
    assert sorted(path.name for path in FOLDER.iterdir()) == sorted(SIDES)
    for folder, side in SIDES.items():
        names = sorted(path.name for path in (FOLDER / folder).iterdir())
        assert names == sorted(name + ".png" for name in render_screens.DIY), AGAIN
        for name in names:
            with Image.open(FOLDER / folder / name) as picture:
                assert picture.format == "PNG" and picture.size == (side, side)


@pytest.mark.skipif(not features.check("raqm"), reason=render_screens.NO_RAQM)
def test_the_diy_pictures_are_what_the_screens_show_now(tmp_path, utc):
    pictures = render_screens.diy(tmp_path)
    assert sorted(pictures) == [128, 240]
    for size, shots in pictures.items():
        assert sorted(shots) == sorted(render_screens.DIY)
        for name, picture in shots.items():
            with Image.open(FOLDER / str(size) / (name + ".png")) as saved:
                assert saved.size == picture.size, AGAIN
                assert not changed(picture, saved), "%d/%s.png has changed. %s" % (size, name, AGAIN)


def test_softer_letter_edges_pass_but_another_color_or_word_does_not():
    def drawn(text, color):
        c = gfx.Canvas((240, 240))
        c.text((5, 30), text, gfx.font("ui", 15), color)
        return c.image

    saved = drawn("Wi-Fi adapters", gfx.MUTED)
    softer = saved.copy()
    for x in range(8):
        softer.putpixel((x, 0), tuple(value + 54 for value in gfx.BG))
    assert not changed(saved, saved) and not changed(softer, saved)
    assert changed(drawn("Wi-Fi adapters", gfx.FAINT), saved)
    assert changed(drawn("Wi-Fi adapters", gfx.AMBER), drawn("Wi-Fi adapters", gfx.GOLD))
    assert changed(drawn("49 KB", gfx.GOLD), drawn("48 KB", gfx.GOLD))


def test_the_diy_pictures_are_never_written_without_raqm(tmp_path, monkeypatch):
    monkeypatch.setattr(render_screens.tones, "find_player", render_screens.tones.find_player)
    monkeypatch.setattr(render_screens.features, "check", lambda name: False)
    monkeypatch.setattr(sys, "argv", ["render_screens.py", "--out", str(tmp_path / "out"), "--diy"])
    with pytest.raises(SystemExit) as stopped:
        render_screens.main()
    assert render_screens.NO_RAQM in str(stopped.value)
    assert not (tmp_path / "out").exists()


def test_no_diy_screen_draws_a_character_its_font_does_not_have(tmp_path, utc, monkeypatch):
    drawn = []
    put = gfx.Canvas.put

    def record(self, xy, text, f, *args, **kwargs):
        drawn.append((one_line(text), f))
        return put(self, xy, text, f, *args, **kwargs)

    monkeypatch.setattr(gfx.Canvas, "put", record)
    render_screens.diy(tmp_path)
    missing = set()
    for text, f in drawn:
        if not hasattr(f, "getmask"):
            continue
        box = bytes(f.getmask("\U0010fffd"))
        missing |= {char for char in set(text) - {" "} if bytes(f.getmask(char)) == box}
    assert drawn and not missing
