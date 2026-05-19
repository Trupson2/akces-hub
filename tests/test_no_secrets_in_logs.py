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
import os
import re

_BASE = os.path.join(os.path.dirname(__file__), "..", "modules")

# Pliki dotykające Allegro token-endpointu (access/refresh token w body)
_FILES = ["allegro_api.py", "token_refresh.py"]

# Linia logująca/alertująca, która jednocześnie zrzuca surowe body odpowiedzi
_BAD = re.compile(
    r"(print|log\w*|_handle_failure|_send_alert|_maybe_send_alert)\s*\("
    r"[^\n]*response\.text"
)


def test_no_raw_response_text_in_log_sinks():
    """Żaden print/log/alert nie może zawierać surowego response.text."""
    offenders = []
    for fname in _FILES:
        path = os.path.abspath(os.path.join(_BASE, fname))
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if _BAD.search(line):
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
