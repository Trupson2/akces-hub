"""
PHASE 3 — testy regresyjne audytu logowania.

Tło: infra `admin_audit_log` + `log_admin_action()` istniała i była
używana (api-key, zmiana roli, restore), ale `login()` audytował
WYŁĄCZNIE `login_turnstile_fail`, a `logout()` nie audytował NIC.
Brak śladu kto/kiedy się zalogował i nieudanych prób = zero
widoczności brute-force i zero rozliczalności dostępu admina
(istotne przy wydaniu „enterprise"/managed-install).

Statyczny (regex na źródle auth.py) — pełny runtime-mock całej ścieżki
login (CSRF, Turnstile, rate-limit, 2FA) byłby kruchy; ta klasa
regresji ("ktoś usunął audyt z login/logout") jest w pełni łapana
statycznie i bez flakiness. Zgodne ze stylem test_phase2_ops.py.
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AUTH = os.path.join(_ROOT, "modules", "auth.py")


def _auth_src():
    return open(_AUTH, encoding="utf-8").read()


def _func_body(src, name):
    """Zwraca źródło funkcji `name` (do następnego top-level def/@route)."""
    m = re.search(rf"\ndef {re.escape(name)}\(", src)
    assert m, f"nie znaleziono def {name}() w auth.py"
    start = m.start()
    nxt = re.search(r"\n@auth_bp\.route\(|\ndef [a-zA-Z_]", src[start + 1 :])
    return src[start : start + 1 + nxt.start()] if nxt else src[start:]


def test_login_audits_success():
    """Udane logowanie MUSI trafić do admin_audit_log (rozliczalność)."""
    body = _func_body(_auth_src(), "login")
    assert "log_admin_action('login_success'" in body, (
        "login() nie audytuje udanego logowania (regresja: brak śladu "
        "kto/kiedy się zalogował)"
    )


def test_login_audits_failure_with_success_false():
    """Nieudane logowanie MUSI być audytowane z success=False
    (widoczność brute-force / enumeracji kont)."""
    body = _func_body(_auth_src(), "login")
    assert "'login_failed'" in body, (
        "login() nie audytuje nieudanej próby (regresja: ślepota na brute-force)"
    )
    # success=False musi towarzyszyć login_failed (nie liczyć jako OK)
    seg = body[body.index("'login_failed'") : body.index("'login_failed'") + 400]
    assert "success=False" in seg, (
        "login_failed bez success=False — nieudana próba zliczana jako sukces"
    )


def test_logout_audits_before_session_clear():
    """logout() MUSI audytować PRZED session.clear() — po wyczyszczeniu
    sesji auto-fill user_id/username/rola = puste (bezużyteczny wpis)."""
    body = _func_body(_auth_src(), "logout")
    assert "log_admin_action('logout')" in body, (
        "logout() nie audytuje wylogowania (regresja)"
    )
    # Pomijamy komentarze — tekst komentarza zawiera literalnie
    # "session.clear()" (wzorzec filtrowania jak w test_phase2_ops.py)
    code = "\n".join(
        ln for ln in body.splitlines() if not ln.strip().startswith("#")
    )
    i_audit = code.index("log_admin_action('logout')")
    i_clear = code.index("session.clear()")
    assert i_audit < i_clear, (
        "audyt logout PO session.clear() — wpis bez user_id/username "
        "(auto-fill z pustej sesji). Audyt MUSI być przed clear()."
    )


def test_audit_calls_are_failsafe():
    """Każdy nowy audyt login/logout owinięty try/except — audyt NIE
    może wywrócić logowania/wylogowania (dostępność > audyt)."""
    src = _auth_src()
    for action in ("'login_success'", "'login_failed'", "'logout'"):
        idx = src.index(f"log_admin_action({action}") if f"log_admin_action({action}" in src else src.index(action)
        # 220 znaków wstecz musi zawierać 'try:' (wzorzec z pliku)
        assert "try:" in src[max(0, idx - 220):idx], (
            f"audyt {action} nie jest w bloku try/except (ryzyko: "
            f"błąd audytu blokuje auth)"
        )
