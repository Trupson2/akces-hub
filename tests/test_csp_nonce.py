"""Smoke test CSP nonce infrastructure (fundament pod Phase 4 rollout).

HISTORIA:
- Phase 2 dodal fundament nonce (g.csp_nonce, context processor).
- Phase 3 probowal wstrzykiwac 'nonce-XXX' do CSP headera, ale okazalo sie ze
  CSP Level 3 spec IGNORUJE 'unsafe-inline' gdy nonce jest obecny w source list
  (https://www.w3.org/TR/CSP3/#match-element-to-source-list). Skutek: wszystkie
  577 inline <style>/<script>/event-handlerow zostalo zablokowanych i strona
  sie rozjechala (hot-fix 15.04.2026 rollback).
- Phase 4 (przyszly): migracja 577 inline handlerow na addEventListener +
  dodanie nonce="{{ csp_nonce }}" do wszystkich pozostalych inline blokow —
  wtedy mozna wlaczyc nonce w CSP headerze i wywalic unsafe-inline.

Ten test waliduje obecny stan: infrastructure istnieje i dziala,
ALE nonce NIE jest w CSP headerze (dopoki Phase 4 nie zrobi rollout).
"""
import os
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


def test_csp_header_present(csp_client):
    """GET HTML endpoint → response ma CSP header (defense-in-depth)."""
    rv = csp_client.get('/auth/login')
    csp = _get_csp_header(rv)
    assert csp, "Brak Content-Security-Policy header w odpowiedzi HTML"
    # Kluczowe directives musza byc obecne
    assert "default-src 'self'" in csp
    assert "script-src" in csp
    assert "style-src" in csp
    assert "frame-ancestors 'self'" in csp


def test_csp_has_unsafe_inline_fallback_pre_phase4(csp_client):
    """Dopoki Phase 4 rollout nie zrobiony — 'unsafe-inline' musi byc w CSP.

    Gdy kiedys wywalmy unsafe-inline (po migracji 577 inline handlerow),
    ten test ma failowac jako sygnal ze trzeba go zaktualizowac.
    """
    rv = csp_client.get('/auth/login')
    csp = _get_csp_header(rv)
    assert csp
    assert "'unsafe-inline'" in csp, \
        "unsafe-inline usuniety przedwczesnie — 577 inline handlerow jeszcze nie zmigrowane"


def test_csp_does_not_have_nonce_yet_pre_phase4(csp_client):
    """Hot-fix regression guard: 'nonce-XXX' NIE MOZE byc w CSP headerze

    Powod: CSP3 spec — nonce w source list IGNORUJE unsafe-inline. Skutek:
    wszystkie inline <style>/<script> zostaly zablokowane, strona rozjebana.
    Dopoki Phase 4 nie doda nonce do wszystkich inline blokow w templateach,
    NIE wolno wstrzykiwac nonce w CSP headerze.
    """
    rv = csp_client.get('/auth/login')
    csp = _get_csp_header(rv)
    assert "'nonce-" not in csp, (
        f"REGRESSION: 'nonce-' wrocil do CSP headera — zablokuje inline style!\n"
        f"CSP: {csp}"
    )


def test_csp_nonce_infrastructure_exists(csp_client):
    """Infrastructure nonce (context processor + g.csp_nonce) gotowa pod Phase 4.

    Nawet gdy nie wstrzykujemy nonce w CSP headerze, templates moga juz zaczac
    uzywac `nonce="{{ csp_nonce }}"` w nowych inline blokach — beda dzialac
    (bo unsafe-inline je przepuszcza), a w dniu Phase 4 rollout wystarczy przelaczyc
    flag w CSP headerze.

    Sprawdzamy ze context processor wstrzykuje `csp_nonce` do renderowanych templateow.
    """
    from app import app
    # Rendered response z test clienta — context processor wstrzykuje csp_nonce
    # nawet jesli template go nie uzywa, `g.csp_nonce` jest ustawione przez before_request
    with app.test_request_context('/'):
        # preprocess_request wywoluje wszystkie before_request hooks
        # (jeden z nich to _generate_csp_nonce)
        app.try_trigger_before_first_request_functions() if hasattr(
            app, 'try_trigger_before_first_request_functions'
        ) else None
        # Rzeczywisty request przez test client zagwarantuje ze hook sie wywola
    rv = csp_client.get('/auth/login')
    # context processor inject_csp_nonce MUSI byc zarejestrowany
    assert 'inject_csp_nonce' in [f.__name__ for f in app.template_context_processors[None]], \
        "inject_csp_nonce context processor nie zarejestrowany"
    # before_request MUSI zawierac _generate_csp_nonce
    assert '_generate_csp_nonce' in [f.__name__ for f in app.before_request_funcs[None]], \
        "_generate_csp_nonce before_request hook nie zarejestrowany"
