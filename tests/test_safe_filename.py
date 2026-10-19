"""Filename sanitisation for the batch zip.

Windows refuses the reserved device names and strips trailing dots or spaces, so
a zip member built from a clip title can fail to extract there. These tests pin
the rules that ``safe_filename`` has to enforce.
"""

import pytest

from backend.utils.text import safe_filename


@pytest.mark.parametrize(
    "raw",
    ['a/b', 'a\\b', 'a:b', 'a*b', 'a?b', 'a"b', 'a<b', 'a>b', 'a|b', "a\tb", "a\x00b"],
)
def test_strips_characters_windows_rejects(raw):
    assert safe_filename(raw) == "ab"


@pytest.mark.parametrize("name", ["CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT9"])
def test_reserved_device_names_are_defused(name):
    result = safe_filename(name)
    assert result != name
    assert not result.upper().startswith(name.upper() + ".")
    assert result.upper().rstrip("_") == name.upper()


def test_trailing_dots_and_spaces_removed():
    assert safe_filename("trailing dot .") == "trailing dot"
    assert safe_filename("trailing space ") == "trailing space"
    assert safe_filename("...") == "clip"


def test_empty_input_falls_back():
    assert safe_filename("") == "clip"
    assert safe_filename("   ") == "clip"
    assert safe_filename("///", fallback="clip_1") == "clip_1"


def test_length_is_capped():
    assert len(safe_filename("x" * 500)) == 120


def test_normal_titles_survive_unchanged():
    # Only the characters Windows rejects are removed; quotes and emoji stay.
    assert safe_filename("EP2: What's Next") == "EP2 What's Next"
    assert safe_filename("emoji \U0001f3ac title") == "emoji \U0001f3ac title"
