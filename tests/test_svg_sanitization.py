"""Test SVG sanitization — odrzuca XSS payloady w plikach SVG.

SVG jest XML — moze zawierac <script>, event handlers, foreignObject z HTML,
xlink:href do javascript:. Jesli admin uplouduje "logo" z tymi elementami,
serwowanie tego SVG przez <object>/<embed>/Content-Type:image/svg+xml moze
wykonac JS w kontekscie naszej domeny.

Sanitizer w modules/utils.sanitize_svg() musi odrzucac wszystkie te wektory.
"""
import os
import sys
import io
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.utils import sanitize_svg


# ============================================================
# JEDNOSTKOWE: bezposrednio sanitize_svg()
# ============================================================

def test_clean_svg_accepted():
    """Czysty SVG (tylko geometria) — przepuszczony."""
    clean = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path d="M0 0L100 100"/></svg>'
    is_safe, reason = sanitize_svg(clean)
    assert is_safe, f"Czysty SVG odrzucony niesluszne: {reason}"


def test_svg_with_script_tag_rejected():
    """SVG z <script>alert(1)</script> — odrzucony."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe
    assert 'script' in reason.lower()


def test_svg_with_onclick_rejected():
    """SVG z event handler onclick="..." — odrzucony."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="alert(1)" width="10" height="10"/></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe
    assert 'event' in reason.lower() or 'on' in reason.lower()


def test_svg_with_onload_rejected():
    """SVG z onload (czesty atak — auto-fire) — odrzucony."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe


def test_svg_with_xlink_javascript_rejected():
    """SVG z xlink:href="javascript:..." — odrzucony."""
    # NOTE: <use> samo w sobie odrzucamy (moze sciagac zewnetrzny SVG)
    payload = b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><a xlink:href="javascript:alert(1)"><rect width="10" height="10"/></a></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe


def test_svg_with_foreignobject_rejected():
    """SVG z <foreignObject> (moze zawierac HTML+JS) — odrzucony.

    Test uzywa foreignObject BEZ <script> w srodku zeby sanitizer odrzucil
    konkretnie foreignObject (a nie <script> ktory jest pierwszy w iteracji).
    """
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject width="100" height="100"><div xmlns="http://www.w3.org/1999/xhtml">html w svg</div></foreignObject></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe
    assert 'foreignobject' in reason.lower()


def test_svg_with_use_external_rejected():
    """SVG z <use href="https://attacker.com/evil.svg"> — odrzucony.

    <use> moze sciagac SVG z dowolnej domeny i wstrzyknac jego zawartosc.
    """
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><use href="https://attacker.com/evil.svg#x"/></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe


def test_svg_with_iframe_rejected():
    """SVG z <iframe> — odrzucony."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><iframe src="//evil"/></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe


def test_svg_with_javascript_uri_rejected():
    """SVG z 'javascript:' w dowolnym atrybucie href — odrzucony."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><text>click</text></a></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe


def test_svg_with_data_href_rejected():
    """SVG z href="data:text/html,..." — odrzucony (zewnetrzny URI)."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:text/html,<script>alert(1)</script>"/></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe


def test_svg_internal_fragment_href_ok():
    """SVG z href="#path1" (fragment do wlasnego elementu) — OK."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><defs><path id="p1" d="M0 0L10 10"/></defs></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert is_safe, f"Fragment href powinien byc OK: {reason}"


def test_svg_empty_rejected():
    """Pusty SVG — odrzucony."""
    is_safe, reason = sanitize_svg(b'')
    assert not is_safe


def test_svg_string_input_works():
    """Sanitizer akceptuje str (nie tylko bytes)."""
    payload = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    is_safe, reason = sanitize_svg(payload)
    assert not is_safe


# ============================================================
# INTEGRACYJNE: handler /setup/logo
# ============================================================

@pytest.fixture
def logo_client():
    """Client z testowa app — /setup/logo nie wymaga sesji (publiczny endpoint)."""
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


def test_upload_malicious_svg_rejected(logo_client):
    """POST /setup/logo z SVG zawierajacym <script> → 400 + audit log."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    rv = logo_client.post(
        '/setup/logo',
        data={'logo': (io.BytesIO(payload), 'evil.svg')},
        content_type='multipart/form-data',
    )
    assert rv.status_code == 400, f"Spodziewano 400, dostano {rv.status_code}"
    json_data = rv.get_json() or {}
    assert json_data.get('ok') is False
    assert 'svg' in (json_data.get('error') or '').lower() or 'odrzu' in (json_data.get('error') or '').lower()


def test_upload_clean_svg_accepted(logo_client, tmp_path, monkeypatch):
    """POST /setup/logo z czystym SVG → 200 + plik zapisany."""
    payload = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40"/></svg>'
    rv = logo_client.post(
        '/setup/logo',
        data={'logo': (io.BytesIO(payload), 'clean.svg')},
        content_type='multipart/form-data',
    )
    # Musi byc 200 (sukces) — clean SVG przeszedl
    assert rv.status_code == 200, f"Czysty SVG powinien zostac zapisany, dostano {rv.status_code}"
    json_data = rv.get_json() or {}
    assert json_data.get('ok') is True


def test_upload_svg_with_event_handler_rejected(logo_client):
    """POST /setup/logo z SVG z onclick="..." → 400."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect width="10" height="10"/></svg>'
    rv = logo_client.post(
        '/setup/logo',
        data={'logo': (io.BytesIO(payload), 'evt.svg')},
        content_type='multipart/form-data',
    )
    assert rv.status_code == 400


def test_upload_svg_with_xlink_javascript_rejected(logo_client):
    """POST /setup/logo z SVG z xlink:href="javascript:..." → 400."""
    payload = b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><a xlink:href="javascript:alert(1)"><rect width="10" height="10"/></a></svg>'
    rv = logo_client.post(
        '/setup/logo',
        data={'logo': (io.BytesIO(payload), 'xlink.svg')},
        content_type='multipart/form-data',
    )
    assert rv.status_code == 400
