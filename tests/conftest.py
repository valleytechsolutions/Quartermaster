import time

import pytest

from quartermaster import faults, radios
from quartermaster.names import one_line
from quartermaster.ui import gfx, keys


@pytest.fixture
def drawn(monkeypatch):
    """Every string a screen draws, so a test can read the screen."""
    texts = []
    original = gfx.Canvas.put

    def record(self, xy, text, *args, **kwargs):
        texts.append(one_line(text))
        return original(self, xy, text, *args, **kwargs)
    monkeypatch.setattr(gfx.Canvas, "put", record)
    return texts


@pytest.fixture
def noted(tmp_path, monkeypatch):
    """The file unexpected errors are noted in."""
    monkeypatch.setattr(faults, "path", tmp_path / "errors.log")
    monkeypatch.setattr(faults, "_recent", {})
    return faults.path


@pytest.fixture(autouse=True)
def free_radios():
    yield
    radios.release("wardrive")
    radios.release("recon")


@pytest.fixture(autouse=True)
def no_installed_keymap(monkeypatch):
    """The keymap file of the machine the tests run on must not decide their result."""
    monkeypatch.setattr(keys, "KEYMAP", "/nonexistent")


@pytest.fixture
def later(monkeypatch):
    """Moves the clock on by a number of seconds, for everything that reads it."""
    real, ahead = time.monotonic, [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real() + ahead[0])

    def move(seconds):
        ahead[0] += seconds
    return move
