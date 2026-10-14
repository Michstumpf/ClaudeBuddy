import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("buddy_ptt", Path(__file__).resolve().parents[1] / "ptt" / "buddy_ptt.py")
ptt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ptt)


@pytest.mark.parametrize("ch, strokes", [
    ("a", [("KEY_A", False)]),
    ("A", [("KEY_A", True)]),
    ("ç", [("KEY_SEMICOLON", False)]),
    ("/", [("KEY_RO", False)]),
    ("?", [("KEY_RO", True)]),
    (";", [("KEY_SLASH", False)]),
    ("'", [("KEY_GRAVE", False)]),
    ('"', [("KEY_GRAVE", True)]),
    ("]", [("KEY_BACKSLASH", False)]),
    ("@", [("KEY_2", True)]),
    ("á", [("KEY_LEFTBRACE", False), ("KEY_A", False)]),
    ("À", [("KEY_LEFTBRACE", True), ("KEY_A", True)]),
    ("ã", [("KEY_APOSTROPHE", False), ("KEY_A", False)]),
    ("ê", [("KEY_APOSTROPHE", True), ("KEY_E", False)]),
    ("ü", [("KEY_6", True), ("KEY_U", False)]),
    ("~", [("KEY_APOSTROPHE", False), ("KEY_SPACE", False)]),
    ("^", [("KEY_APOSTROPHE", True), ("KEY_SPACE", False)]),
    ("😀", None),
])
def test_abnt2_strokes(ch, strokes):
    assert ptt.abnt2_strokes(ch) == strokes


def test_every_portuguese_letter_is_typeable():
    for ch in "áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ":
        assert ptt.abnt2_strokes(ch), ch


class FakeTyper(ptt.UInputTyper):
    def __init__(self):
        self.pressed = []

    def _press(self, key, shift):
        self.pressed.append((key, shift))


def test_typer_sends_keys_and_skips_untypeable(monkeypatch):
    monkeypatch.setattr(ptt, "DEAD_KEY_SETTLE", 0)
    typer = FakeTyper()
    typer.type("Não “é” 😀")
    assert typer.pressed == [
        ("KEY_N", True), ("KEY_APOSTROPHE", False), ("KEY_A", False), ("KEY_O", False), ("KEY_SPACE", False),
        ("KEY_GRAVE", True), ("KEY_LEFTBRACE", False), ("KEY_E", False), ("KEY_GRAVE", True), ("KEY_SPACE", False),
    ]
