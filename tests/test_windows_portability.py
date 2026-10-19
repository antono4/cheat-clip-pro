"""Windows x64 portability invariants.

Windows runs the app as a desktop product (``start.bat``, winget FFmpeg), but
its default text encoding is the ANSI code page, not UTF-8. That makes a few
things that "just work" on Linux fail only on Windows, so they are pinned here.

The suite itself runs on Windows x64 in CI (``backend-windows`` job); these
tests are the unit-level half of that coverage and run on every platform.
"""

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent / "backend"

# subprocess entry points that accept the text/encoding keywords.
_SUBPROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}


def _backend_modules():
    return sorted(BACKEND.rglob("*.py"))


def _subprocess_text_calls():
    """Yield (path, lineno, has_encoding) for every text-mode subprocess call."""
    for path in _backend_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match ``subprocess.run`` / ``subprocess.Popen`` attribute calls.
            if not (
                isinstance(func, ast.Attribute)
                and func.attr in _SUBPROCESS_CALLS
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            ):
                continue
            keywords = {kw.arg for kw in node.keywords}
            is_text = "text" in keywords or "universal_newlines" in keywords
            if is_text:
                yield path, node.lineno, "encoding" in keywords


def test_every_text_mode_subprocess_call_pins_utf8():
    """Decoding child output must not fall back to the Windows ANSI code page.

    yt-dlp and ffmpeg emit UTF-8. On Windows a text-mode subprocess with no
    explicit ``encoding`` decodes with cp1252, so any non-Latin-1 byte (a clip
    title in Portuguese, Turkish, Cyrillic, CJK, or an emoji) raises
    ``UnicodeDecodeError`` and the download/render fails. Passing
    ``encoding="utf-8"`` (with ``errors="replace"``) makes the same call work on
    every platform.
    """
    offenders = [
        f"{path.relative_to(BACKEND.parent)}:{lineno}"
        for path, lineno, has_encoding in _subprocess_text_calls()
        if not has_encoding
    ]

    assert not offenders, (
        "text-mode subprocess calls must pass encoding='utf-8' (Windows decodes "
        "with the ANSI code page otherwise): " + ", ".join(offenders)
    )


def test_the_audit_actually_sees_the_calls():
    """Guard the guard: a broken AST match would make the check vacuously pass."""
    calls = list(_subprocess_text_calls())

    assert len(calls) >= 10, f"expected many text-mode subprocess calls, saw {len(calls)}"


@pytest.mark.parametrize(
    "title",
    [
        "S\u0101o Paulo trip",  # macron -> 0x81, undefined in cp1252
        "\u0141\u00f3d\u017a",  # Polish L-stroke
        "\u0401",               # Cyrillic Yo
        "\u03d0",               # Greek beta symbol -> 0x90
    ],
)
def test_utf8_bytes_are_not_cp1252_decodable(title):
    """Why the encoding pin matters: these UTF-8 titles crash a cp1252 decode."""
    raw = title.encode("utf-8")

    with pytest.raises(UnicodeDecodeError):
        raw.decode("cp1252")

    assert raw.decode("utf-8", errors="replace") == title
