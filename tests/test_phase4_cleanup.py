"""
PHASE 4 — testy regresyjne porządków przedwydaniowych.

1) invalid escape sequence: `'\\s'` w NIE-raw stringu (zwykle regex JS
   wklejony w szablon) = DeprecationWarning (3.11) → SyntaxWarning (3.12)
   → docelowo SyntaxError. Runtime-wartość była i jest identyczna
   (Python przepuszcza nieznany escape), więc podwojenie backslasha
   (`\\s`→`\\\\s`) jest bezpieczne i wycisza. Test pilnuje że nikt nie
   wklei znów surowego regexa do nie-raw stringa.

2) CHANGELOG mojibake: `_generate_changelog()` poprawnie zapisywał plik
   jako utf-8, ale `subprocess.run(text=True)` BEZ jawnego encoding
   dekoduje wyjście gita locale-em (cp1250 na Win) → mojibake.
   Test pilnuje że subprocess ma jawne encoding='utf-8'.
"""
import ast
import glob
import os
import re
import warnings

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PY = sorted(glob.glob(os.path.join(_ROOT, "modules", "*.py"))) + [
    os.path.join(_ROOT, "app.py")
]


def test_no_invalid_escape_sequences():
    """Zero invalid-escape w modules/*.py + app.py (future-proof 3.12)."""
    offenders = []
    for path in _PY:
        src = open(path, encoding="utf-8").read()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                compile(src, path, "exec")
            except SyntaxError:
                continue  # łapie test_phase3_syntax.py
            for w in caught:
                if "escape sequence" in str(w.message):
                    offenders.append(
                        f"{os.path.basename(path)}:{w.lineno}: {w.message}"
                    )
    assert not offenders, (
        "invalid escape sequence (nie-raw string z regexem JS?) — "
        "podwój backslash lub użyj r'...':\n" + "\n".join(offenders)
    )


def test_changelog_generator_forces_utf8():
    """_generate_changelog() MUSI dekodować wyjście gita jako utf-8
    (bez tego: mojibake przy locale != utf-8, np. Windows cp1250)."""
    app_src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    m = re.search(r"def _generate_changelog\(\):.*?\n(?=\S|# PERF)", app_src, re.S)
    assert m, "nie znaleziono _generate_changelog()"
    body = m.group(0)
    assert "subprocess.run(" in body, "generator nie woła subprocess.run"
    # w wywołaniu subprocess.run musi być jawne encoding='utf-8'
    call = body[body.index("subprocess.run("):]
    call = call[: call.index(")") + 1] if ")" in call else call
    # bierzemy do zamykającego nawiasu wywołania (multiline)
    depth = 0
    end = 0
    start = body.index("subprocess.run(") + len("subprocess.run")
    for i, ch in enumerate(body[start:], start):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    run_call = body[start : end + 1]
    assert "encoding='utf-8'" in run_call or 'encoding="utf-8"' in run_call, (
        "subprocess.run w _generate_changelog bez encoding='utf-8' — "
        "ryzyko mojibake w CHANGELOG.md przy locale != utf-8"
    )


def test_changelog_file_has_no_mojibake():
    """Wygenerowany CHANGELOG.md (jeśli jest) nie zawiera markerów
    mojibake (UTF-8 zdekodowane jako cp125x)."""
    cl = os.path.join(_ROOT, "CHANGELOG.md")
    if not os.path.exists(cl):
        return  # generowany w runtime na serwerze — brak w repo OK
    txt = open(cl, encoding="utf-8", errors="replace").read()
    bad = re.findall(r"â€|Ä…|Ĺ‚|Ä‡|Å„|Ã³|Ä™|Ĺ›|Ĺ¼", txt)
    assert not bad, (
        f"CHANGELOG.md zawiera mojibake ({len(bad)} trafień) — "
        "regeneruj fixniętym _generate_changelog()"
    )
