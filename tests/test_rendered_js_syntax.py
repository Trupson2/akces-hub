"""
Test regresyjny: inline JS w modułach renderujących MUSI być składniowo
poprawny PO wykonaniu przez Python (runtime), nie tylko w źródle.

Tło (incydent 2026-05): `szczegoly_js = '''... onclick="menuStatus(\\'x\\')" ...'''`
w palety.py. Autor zakładał JS-escape `\\'`, ale to zwykły Python string —
Python zjada `\\'` -> `'`, więc RUNTIME-wartość to zepsuty JS
(`menuStatus('x')` wewnątrz '...' -> SyntaxError: Unexpected string).
`node --check` na ŹRÓDLE-jako-tekście tego NIE łapie (widzi `\\'` = legalny
JS). Trzeba exec-ować Python i sprawdzać WYNIK. Ten test to robi.

Pokrywa statyczne bloki `<nazwa>_js = '''...'''` (nie f-string — te wymagają
kontekstu renderu; osobne zadanie). Skip gdy `node` niedostępny.
"""
import os
import re
import shutil
import subprocess
import tempfile

import pytest

MODULES = [
    os.path.join(os.path.dirname(__file__), "..", "modules", "palety.py"),
]

# (plik, nazwa_zmiennej) statycznych blokow JS do sprawdzenia
_BLOCK_RE = re.compile(r"^\s*(\w+_js) = '''", re.M)


def _iter_static_js_blocks():
    for path in MODULES:
        path = os.path.abspath(path)
        src = open(path, encoding="utf-8").read()
        for m in _BLOCK_RE.finditer(src):
            name = m.group(1)
            s = m.start(1)  # start nazwy zmiennej (BEZ wiodacych spacji -
            #                 inaczej exec() -> IndentationError)
            nxt = src.find("return render(", s)
            blk = src[s:nxt] if nxt > 0 else src[s : s + 60000]
            stmt = blk[: blk.rfind("'''") + 3]
            yield (os.path.basename(path), name, stmt)


_CASES = list(_iter_static_js_blocks())


@pytest.mark.skipif(shutil.which("node") is None, reason="node niedostepny")
@pytest.mark.parametrize(
    "fname,name,stmt",
    _CASES,
    ids=[f"{f}:{n}" for f, n, _ in _CASES],
)
def test_rendered_js_is_valid_syntax(fname, name, stmt):
    """Runtime-wartosc bloku JS musi przejsc `node --check`."""
    ns: dict = {}
    exec(stmt, ns)  # noqa: S102 - kontrolowany literal ze zrodla repo
    js = ns[name]
    assert isinstance(js, str) and js.strip(), f"{fname}:{name} pusty"

    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(js)
        tmp = fh.name
    try:
        r = subprocess.run(
            ["node", "--check", tmp], capture_output=True, text=True, timeout=30
        )
        assert r.returncode == 0, (
            f"{fname}:{name} — RUNTIME JS ma blad skladni "
            f"(Python zjadl escape?):\n{r.stderr}"
        )
    finally:
        os.unlink(tmp)


def test_at_least_one_block_found():
    """Sanity: regex nadal znajduje bloki (ochrona przed cichym 0-pokryciem)."""
    assert _CASES, "Nie znaleziono zadnych statycznych blokow *_js = '''...'''"
