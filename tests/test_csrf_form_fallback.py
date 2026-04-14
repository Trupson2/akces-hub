"""Test CSRF form-POST fallback — luka znaleziona w Phase 2 audit.

Problem: middleware w app.py (csrf_protect_forms) akceptowal form POST BEZ csrf_token
w body bez zadnego sprawdzenia (gałąź else nie istniala). To pozwalalo na CSRF z
zewnetrznej domeny przez prosty <form action=https://akces/... method=POST> bez JS.

Fix: jesli brak tokena w body, wymagaj same-origin Referer; jak nie, abort(403).
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def csrf_client():
    """Specjalny client z WYLACZONYM AKCES_TEST_MODE — chcemy przetestowac
    realnie middleware CSRF (ktory normalnie pomija test mode)."""
    # Ustaw AKCES_TEST_MODE tylko dla zaladowania app (bypass licencji/eula)
    os.environ['AKCES_TEST_MODE'] = '1'
    try:
        from app import app
        app.config['TESTING'] = True
        # WTF_CSRF_ENABLED=True zeby validate_csrf dzialalo,
        # ale i tak nie weryfikujemy konkretnie tokena — tylko gałąź "brak tokena"
        app.config['WTF_CSRF_ENABLED'] = False  # bo nie testujemy validate_csrf, tylko branchowanie
        # Uzywamy SECRET_KEY (powinno byc juz ustawione przez app)
        with app.test_client() as client:
            with client.session_transaction() as sess:
                # Symuluj zalogowanego usera (CSRF middleware sprawdza user_id)
                sess['user_id'] = 1
                sess['rola'] = 'admin'
            # WAZNE: po przygotowaniu zalogowanej sesji, wylacz AKCES_TEST_MODE
            # zeby gałąź CSRF middleware nie byla pominieta
            os.environ.pop('AKCES_TEST_MODE', None)
            yield client
    except Exception as e:
        os.environ.pop('AKCES_TEST_MODE', None)
        pytest.skip(f'Flask app nie zaladowana: {e}')
    finally:
        os.environ.pop('AKCES_TEST_MODE', None)


def test_form_post_no_token_no_referer_blocked(csrf_client):
    """Anonimowy POST z formularzem BEZ csrf_token i BEZ Referer → 403.

    To jest rdzenny test luki: cross-origin attacker wysyla form POST,
    Referer jest pusty (lub z innej domeny) i nie ma tokena → musi byc 403.
    """
    rv = csrf_client.post(
        '/api/jakikolwiek-endpoint',
        data={'foo': 'bar'},  # form data, no csrf_token
        content_type='application/x-www-form-urlencoded',
        # Brak Referer header
    )
    # Middleware MUSI zwrocic 403 z powodu CSRF (nawet jesli endpoint nie istnieje, middleware bije pierwszy)
    assert rv.status_code == 403, f"Spodziewano 403, dostano {rv.status_code} (luka CSRF nadal otwarta!)"


def test_form_post_no_token_cross_origin_referer_blocked(csrf_client):
    """POST bez tokena z Referer z OBCEJ domeny → 403."""
    rv = csrf_client.post(
        '/api/jakikolwiek',
        data={'foo': 'bar'},
        content_type='application/x-www-form-urlencoded',
        headers={'Referer': 'https://attacker.example.com/evil.html'},
    )
    assert rv.status_code == 403, f"Cross-origin Referer powinien byc odrzucony, dostano {rv.status_code}"


def test_form_post_no_token_same_origin_referer_ok(csrf_client):
    """POST bez tokena ale z SAME-ORIGIN Referer → middleware przepuszcza
    (404/405 OK — nie 403, bo CSRF check pass).

    Same-origin Referer to fallback dla starszych form-only flow.
    """
    # Uzywamy http://localhost (default test client host_url)
    rv = csrf_client.post(
        '/api/nieistniejacy-endpoint-xyz',
        data={'foo': 'bar'},
        content_type='application/x-www-form-urlencoded',
        headers={'Referer': 'http://localhost/jakas-strona'},
        base_url='http://localhost',
    )
    # NIE moze byc 403 (CSRF musi przepuscic same-origin)
    # Endpoint nie istnieje — moze byc 404 lub redirect na /auth/login (302)
    assert rv.status_code != 403, f"Same-origin Referer mial przejsc CSRF check, dostano 403 (false positive)"


def test_form_post_with_csrf_token_validated(csrf_client):
    """POST z csrf_token w body → middleware probuje validate_csrf.

    Z WTF_CSRF_ENABLED=False validate_csrf jest no-op, wiec request przejdzie
    do kolejnych middleware (nie 403 z CSRF middleware).
    """
    rv = csrf_client.post(
        '/api/nieistniejacy',
        data={'csrf_token': 'fake-but-present', 'foo': 'bar'},
        content_type='application/x-www-form-urlencoded',
    )
    # Z token w body i WTF_CSRF_ENABLED=False — CSRF middleware nie zwroci 403
    # (endpoint moze nie istniec — 404, lub redirect — 302, ale NIE 403 od CSRF)
    assert rv.status_code != 403, f"Token w body — middleware mial przepuscic, dostano 403"
