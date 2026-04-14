"""Smoke test CSP nonce infrastructure (Phase 2 — fundament dla Phase 3).

Phase 2 dodaje nonce do CSP, ale wciaz zostawia 'unsafe-inline' jako fallback
(577 inline event handlerow do migracji w Phase 3). Test sprawdza:
1. CSP header zawiera nonce- prefix dla script-src i style-src
2. Nonce zmienia sie miedzy requestami (nie cachowany)
3. unsafe-inline nadal jest obecny (bo tak ma byc w tej fazie)
"""
import os
import re
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def csp_client():
    """Standardowy test client (wymaga AKCES_TEST_MODE bo niezalogowany)."""
    os.environ['AKCES_TEST_MODE'] = '1'
    try:
        from app import app
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        with app.test_client() as client:
            yield client
    except Exception as e:
        pytest.skip(f'Flask app nie zaladowana: {e}')
    finally:
        os.environ.pop('AKCES_TEST_MODE', None)


def _get_csp_header(rv):
    return rv.headers.get('Content-Security-Policy', '')


def _extract_nonce(csp_header):
    """Wyciagnij wartosc nonce z CSP header. Zwroc None jesli brak."""
    m = re.search(r"'nonce-([A-Za-z0-9_-]+)'", csp_header)
    return m.group(1) if m else None


def test_csp_header_contains_nonce(csp_client):
    """GET dowolny endpoint HTML → response ma CSP header z nonce- prefix."""
    rv = csp_client.get('/')
    csp = _get_csp_header(rv)
    # CSP jest ustawiany tylko dla text/html — sprawdz czy odpowiedz to HTML
    if not csp:
        # Jesli to redirect (302) bez body HTML — sprobuj inny endpoint
        rv = csp_client.get('/auth/login')
        csp = _get_csp_header(rv)
    assert csp, "Brak Content-Security-Policy header w odpowiedzi HTML"
    assert "'nonce-" in csp, f"CSP nie zawiera 'nonce-' prefix:\n{csp}"
    # Nonce musi byc obecne w script-src i style-src
    # (sprawdzamy ze wystepuje min. raz — moze byc raz dla obu)
    nonce = _extract_nonce(csp)
    assert nonce is not None, "Nie znaleziono wartosci nonce"
    assert len(nonce) >= 16, f"Nonce za krotkie ({len(nonce)} chars), spodziewano >=16"


def test_csp_nonce_changes_between_requests(csp_client):
    """Dwa kolejne GET → dwa rozne nonce values (nie cache).

    Krytyczne: jesli nonce sie powtarza, attacker moze go uzyc w kolejnym
    requeste (predictable nonce = no protection).
    """
    rv1 = csp_client.get('/auth/login')
    rv2 = csp_client.get('/auth/login')
    csp1 = _get_csp_header(rv1)
    csp2 = _get_csp_header(rv2)
    nonce1 = _extract_nonce(csp1)
    nonce2 = _extract_nonce(csp2)
    assert nonce1 is not None and nonce2 is not None, "Nonce nie znaleziony"
    assert nonce1 != nonce2, f"Nonce powtorzony miedzy requestami! '{nonce1}' == '{nonce2}'"


def test_csp_still_has_unsafe_inline_phase2(csp_client):
    """Phase 2: 'unsafe-inline' nadal w CSP (fallback do czasu Phase 3 refactora).

    Ten test BEDZIE failowac w Phase 3 — wtedy nalezy go usunac/zmienic.
    """
    rv = csp_client.get('/auth/login')
    csp = _get_csp_header(rv)
    assert csp, "Brak CSP header"
    assert "'unsafe-inline'" in csp, "unsafe-inline usuniety przedwczesnie (Phase 3 nie wykonany)"


def test_csp_nonce_in_both_script_and_style(csp_client):
    """Nonce musi byc w script-src I style-src (oba wsparte w Phase 3 rollout)."""
    rv = csp_client.get('/auth/login')
    csp = _get_csp_header(rv)
    # script-src i style-src — sprawdz ze nonce- jest po obu
    # (regex: szuka wystapien 'nonce-XXX' po script-src i po style-src)
    script_match = re.search(r"script-src[^;]*'nonce-[A-Za-z0-9_-]+'", csp)
    style_match = re.search(r"style-src[^;]*'nonce-[A-Za-z0-9_-]+'", csp)
    assert script_match, f"Brak nonce w script-src:\n{csp}"
    assert style_match, f"Brak nonce w style-src:\n{csp}"
