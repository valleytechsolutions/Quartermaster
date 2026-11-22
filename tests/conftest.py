import pytest

from quartermaster import faults, radios
from quartermaster.names import one_line
from quartermaster.ui import gfx


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
