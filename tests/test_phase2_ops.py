"""
PHASE 2 — testy regresyjne hardeningu operacyjnego.

Pokrywa: healthcheck (HTTP 503 przy DB-fail + brak leak), backup
single-source (koniec duplikatu 2x/h), production startup assumptions
(waitress), głośny fallback. Statyczne tam gdzie mockowanie 14K app.py
byłoby kruche — regresję klasy łapie w pełni i bez flakiness.
"""
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_ROOT, "app.py")


def _app_src():
    return open(_APP, encoding="utf-8").read()


# ── Healthcheck ──────────────────────────────────────────────────────

def test_health_happy_returns_200(app_client):
    """/api/health z działającą DB → HTTP 200 + status ok."""
    r = app_client.get("/api/health")
    assert r.status_code == 200, f"zdrowy health ma być 200, jest {r.status_code}"
    body = r.get_json(silent=True) or {}
    assert body.get("db_status") == "ok"
    assert body.get("status") == "ok"


def test_health_returns_503_on_db_fail_static():
    """Kod /api/health MUSI zwracać 503 gdy DB padła (nie zawsze 200).
    Regresja: monitoring zewn. patrzy na HTTP code, nie body."""
    src = _app_src()
    # blok return api_health musi mieć warunkowy status 200/503
    assert re.search(
        r"\}\),\s*\(200 if db_status == 'ok' else 503\)", src
    ), "api_health nie zwraca 503 przy db_status != ok (regresja monitoringu)"


def test_health_no_exception_leak_static():
    """db_status w publicznym body NIE może zawierać surowego {e}
    (leak ścieżek/SQL — duch PHASE 1)."""
    src = _app_src()
    assert "db_status = f'error: {e}'" not in src, (
        "api_health wycieka exception do publicznego body (PHASE 1 regresja)"
    )
    assert "db_status = 'error'" in src, "brak generycznego db_status='error'"


# ── Backup single-source (koniec duplikatu) ──────────────────────────

def test_backup_no_duplicate_loop_static():
    """hourly_backup() NIE może wołać create_backup() — backup robi
    WYŁĄCZNIE daemon backup_manager. Regresja: 2x backup/h."""
    src = _app_src()
    m = re.search(r"def hourly_backup\(\):.*?(?=\n    threading\.Thread\(target=hourly_backup)", src, re.S)
    assert m, "nie znaleziono hourly_backup()"
    # tylko realny kod — pomijamy komentarze (wyjaśniają DLACZEGO usunięto)
    code_lines = [
        ln for ln in m.group(0).splitlines() if not ln.strip().startswith("#")
    ]
    assert "create_backup()" not in "\n".join(code_lines), (
        "hourly_backup() znów woła create_backup() — duplikat backupu "
        "(2x/h: daemon + ta pętla). Backup ma być tylko w backup_manager."
    )
    # daemon backup_manager nadal uruchamiany (jedyne źródło backupu)
    assert "start_backup_daemon()" in src, "brak start_backup_daemon (backup daemon)"


# ── Production startup assumptions ────────────────────────────────────

def test_waitress_in_requirements():
    """Produkcja zakłada waitress; Flask dev to last-resort fallback."""
    req = open(os.path.join(_ROOT, "requirements.txt"), encoding="utf-8").read()
    assert re.search(r"^\s*waitress", req, re.M), (
        "waitress musi być w requirements.txt (produkcyjny WSGI)"
    )


def test_dev_fallback_is_loud_static():
    """Fallback na Flask dev server MUSI być głośny ([CRITICAL] banner)
    — operator nie może przeoczyć że produkcja chodzi na dev."""
    src = _app_src()
    except_block = src.split("except ImportError:")[-1][:600]
    assert "[CRITICAL]" in except_block and "WAITRESS" in except_block.upper(), (
        "fallback dev-server nie jest głośny ([CRITICAL] banner) — "
        "operator nie zauważy braku waitress w produkcji"
    )


# ── Restore — bezpieczeństwo (path-traversal pokryty osobno) ──────────

def test_restore_function_exists():
    """restore_backup() istnieje i przyjmuje nazwę pliku (smoke)."""
    bm = os.path.join(_ROOT, "modules", "backup_manager.py")
    src = open(bm, encoding="utf-8").read()
    assert re.search(r"def restore_backup\(backup_filename\)", src), (
        "brak restore_backup(backup_filename) — restore niemożliwy"
    )
