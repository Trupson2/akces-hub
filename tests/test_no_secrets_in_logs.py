"""
Test regresyjny (PHASE 1.1): sekrety/tokeny NIE mogą trafiać do logów.

Tło: Allegro token-endpoint przy 200 zwraca access+refresh_token w body,
a przy błędzie WKLEJA fragment refresh_token w error_description
("Invalid refresh token: eyJ..."). Logowanie surowego `response.text`
z tych miejsc = token w journald/nohup.out/Telegram (RODO + kompromitacja
konta Allegro klienta). Ten test pilnuje, by nikt tego nie przywrócił.

Statyczny (regex na źródle) — pełny runtime-mock byłby dokładniejszy, ale
ta klasa regresji ("ktoś znów wstawił response.text do print/_handle_
failure/_send_alert") jest w pełni łapana statycznie i zero-kosztowo.
"""
import glob
import os
import re

_BASE = os.path.join(os.path.dirname(__file__), "..", "modules")

# PHASE 1.1+: skan CALEGO modules/ (nie tylko allegro/token_refresh).
# Szerszy scan wykryl OLX token-refresh (olx_api.py) + telegram_bot +
# gemini (utils/title_generator) logujace response.text — wszystkie
# naprawione. Test pilnuje calej klasy globalnie, w kazdym module.
_FILES = sorted(
    os.path.basename(p)
    for p in glob.glob(os.path.join(_BASE, "*.py"))
)

# Linia logująca/alertująca zrzucająca surowe body odpowiedzi.
# PHASE 1.1++: lapie OBA warianty nazwy zmiennej (response./resp.) ORAZ
# .text i .content (poprzedni regex tylko response.text — przeoczyl
# resp.text w olx/vinted/gpsr/gemini/perplexity, wykryte szerszym scanem).
_BAD = re.compile(
    r"(print|log\w*|logger\.|logging\.|_handle_failure|_send_alert"
    r"|_maybe_send_alert)\s*\([^\n]*\b(response|resp)\.(text|content)\b"
)
# Bezpieczne: len(resp.text) = DLUGOSC (liczba), nie tresc; oraz
# linie juz zsanityzowane (marker "body ukryty"/_safe_resp_err).
_OK = re.compile(r"len\((response|resp)\.(text|content)\)|body ukryty|_safe_resp_err")


def test_no_raw_response_text_in_log_sinks():
    """Żaden print/log/alert nie loguje surowego response/resp .text/.content."""
    offenders = []
    for fname in _FILES:
        path = os.path.abspath(os.path.join(_BASE, fname))
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if _BAD.search(line) and not _OK.search(line):
                    offenders.append(f"{fname}:{i}: {line.strip()[:120]}")
    assert not offenders, (
        "Surowe response.text w log/alert sink (token leak — PHASE 1.1):\n"
        + "\n".join(offenders)
    )


def test_safe_helper_exists():
    """_safe_resp_err musi istnieć w allegro_api.py (bezpieczny komunikat)."""
    path = os.path.abspath(os.path.join(_BASE, "allegro_api.py"))
    src = open(path, encoding="utf-8").read()
    assert "def _safe_resp_err(" in src, (
        "Brak _safe_resp_err — helper maskujący błędy token-endpointu"
    )


# PHASE 1.1++ (post-deploy): taint-flow var = response.text -> print(var).
# Wcześniejszy regex _BAD łapał tylko BEZPOŚREDNIE log/print(response.text).
# Bug w produkcji (allegro_api.py:413 `last_error = response.text[:200]`
# potem `print(f"...{last_error}")`) prześliznął się — variable jako
# pośrednik. Test rozszerzony: znajdź wszystkie `var = response.text...`,
# a potem czy `var` trafia do log/print sink w tym samym pliku.
_VAR_ASSIGN = re.compile(
    r"^\s*(\w+)\s*=\s*\b(response|resp)\.(text|content)\b"
)


def test_no_response_text_via_variable():
    """Pattern przez zmienna posrednia (taint-flow): var=response.text -> log(var)."""
    offenders = []
    for fname in _FILES:
        path = os.path.abspath(os.path.join(_BASE, fname))
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()

        # 1) znajdz wszystkie assignmenty `var = response.text[...]`
        tainted = {}  # var_name -> line_no
        for i, line in enumerate(lines, 1):
            m = _VAR_ASSIGN.match(line)
            if m and not _OK.search(line):
                tainted[m.group(1)] = i

        if not tainted:
            continue

        # 2) czy ktoras tainted variable trafia do print/log w tym samym pliku
        log_sink = re.compile(
            r"(print|log\w*|logger\.|logging\.|_send_alert|_handle_failure"
            r"|_maybe_send_alert)\s*\([^\n]*\b("
            + "|".join(map(re.escape, tainted.keys()))
            + r")\b"
        )
        for i, line in enumerate(lines, 1):
            m = log_sink.search(line)
            if m and not _OK.search(line) and i not in tainted.values():
                var = m.group(2)
                offenders.append(
                    f"{fname}:{tainted[var]} '{var} = response.text...' "
                    f"-> sink:{i} {line.strip()[:80]}"
                )
    assert not offenders, (
        "var = response.text -> log(var) (taint-flow leak, PHASE 1.1++ post-deploy):\n"
        + "\n".join(offenders)
    )
