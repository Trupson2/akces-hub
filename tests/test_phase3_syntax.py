"""
PHASE 3 — testy regresyjne integralności składniowej.

Tło: commit `ef618fa` ("absolute stabilization of search...") wkleił
funkcję `_gemini_search_fallback` DO ŚRODKA pętli `for item in
batch_items:` w `_gemini_discover_trends`, ze słowem `cdef` (Cython!)
zamiast `def`, kasując przy tym ogon `_gemini_discover_trends`
(`return products`). Plik był NIEKOMPILOWALNY (SyntaxError linia 729)
przez wiele commitów — scheduler winning_scout NIE wstawał, a `git
clone`/`git archive` paczkowały zepsuty moduł do klienta.

Ten test pilnuje CAŁEJ KLASY: żaden moduł w repo nie może mieć
SyntaxError, nigdzie nie ma składni Cython (`cdef`/`cpdef`) w czystym
Pythonie, a obie naprawione funkcje muszą być wywoływalne na poziomie
modułu (nie zagnieżdżone wskutek złego merge'u).
"""
import ast
import glob
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULES = os.path.join(_ROOT, "modules")

_PY_FILES = sorted(
    glob.glob(os.path.join(_MODULES, "*.py"))
    + [os.path.join(_ROOT, "app.py")]
)

# `cdef`/`cpdef` jako początek instrukcji = Cython. To repo jest czystym
# Pythonem (brak .pyx, brak setup z cythonize) — taki token to ZAWSZE
# artefakt zepsutego merge'u (dokładnie sygnatura incydentu ef618fa).
_CDEF = re.compile(r"^\s*c(p)?def\s", re.M)


@pytest.mark.parametrize("path", _PY_FILES, ids=lambda p: os.path.basename(p))
def test_module_has_no_syntax_error(path):
    """Każdy .py w modules/ + app.py MUSI się parsować (zero SyntaxError).
    Regresja: zepsuty moduł trafia do klienta przez git archive/clone."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        ast.parse(src, filename=path)
    except SyntaxError as e:
        pytest.fail(
            f"{os.path.basename(path)} ma SyntaxError "
            f"(linia {e.lineno}): {e.msg} — niekompilowalny moduł "
            f"NIE może wejść do wydania"
        )


def test_no_cython_cdef_in_pure_python():
    """Nigdzie w repo nie może być `cdef`/`cpdef` — to czysty Python.
    `cdef` to dokładny ślad incydentu ef618fa (def → cdef przy złym
    merge'u funkcji w pętlę)."""
    offenders = []
    for path in _PY_FILES:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if _CDEF.match(line):
                    offenders.append(f"{os.path.basename(path)}:{i}: {line.strip()[:80]}")
    assert not offenders, (
        "Składnia Cython (cdef/cpdef) w czystym Pythonie — "
        "ślad zepsutego merge'u (klasa incydentu ef618fa):\n"
        + "\n".join(offenders)
    )


def test_winning_scout_functions_are_module_level():
    """`_gemini_discover_trends` i `_gemini_search_fallback` MUSZĄ być
    funkcjami top-level. Bug ef618fa wkleił drugą DO pętli w pierwszej —
    AST łapie to niezależnie od tego czy plik akurat się kompiluje."""
    path = os.path.join(_MODULES, "winning_scout.py")
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    top_level = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    for fn in ("_gemini_discover_trends", "_gemini_search_fallback"):
        assert fn in top_level, (
            f"{fn} nie jest funkcją modułową winning_scout.py "
            f"(regresja: zagnieżdżona wskutek złego merge'u — incydent ef618fa)"
        )


def test_winning_scout_imports_and_callable():
    """Smoke: winning_scout importuje się i obie funkcje są wywoływalne
    (pełny exec modułu, nie tylko parse)."""
    import importlib.util

    path = os.path.join(_MODULES, "winning_scout.py")
    spec = importlib.util.spec_from_file_location("winning_scout_regr", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"winning_scout.py nie importuje się: {e!r}")
    assert callable(mod._gemini_discover_trends)
    assert callable(mod._gemini_search_fallback)


def test_winning_scout_no_token_leak_in_fallback():
    """PHASE 1.1++ regresja: `_gemini_search_fallback` NIE może logować
    surowego resp.text (sygnatura sprzed mojego fixu była
    `{resp.status_code} - {resp.text[:100]}`)."""
    path = os.path.join(_MODULES, "winning_scout.py")
    src = open(path, encoding="utf-8").read()
    assert "resp.text[:100]" not in src, (
        "winning_scout loguje surowy resp.text (PHASE 1.1 regresja)"
    )
    assert "body ukryty — PHASE 1.1+" in src, (
        "brak zsanityzowanego komunikatu AI API Error (PHASE 1.1++ cofnięte)"
    )
