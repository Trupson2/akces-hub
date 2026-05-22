"""Tests for modules.sklepakces_push — Hub → sklepakces.pl WC OUTGOING sync.

Pokrywa:
- mapping Hub `produkty` row → plugin REST payload
- validation (sku regex, condition whitelist, required fields)
- HMAC headers + canonical signing przy POST
- idempotency via mirror table sklepakces_products
- error handling (network fail, invalid payload, missing config)
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from modules.sklepakces_push import (
    _build_sku,
    _norm_kategoria,
    _norm_stan,
    already_synced,
    map_hub_to_plugin,
    push_product,
    record_log,
    record_sync,
    validate_payload,
    ENDPOINT_URL_PATH,
    ENDPOINT_CANONICAL_PATH,
)


# ──────────────────────────────────────────────────────────────────────────────
# _norm_stan — Hub stan → plugin condition mapping
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    ('Nowy', 'nowy'),
    ('nowy', 'nowy'),
    ('Jak nowy', 'jak-nowy'),
    ('jak nowy', 'jak-nowy'),
    ('Jak-Nowy', 'jak-nowy'),
    ('Używany', 'uzywane'),
    ('uzywany', 'uzywane'),
    ('Używane', 'uzywane'),
    ('Ślady używania', 'slady-uzywania'),
    ('slady uzywania', 'slady-uzywania'),
    ('Uszkodzony', 'slady-uzywania'),  # mapped do "ślady używania" (closest in plugin whitelist)
    ('nieoceniony', 'jak-nowy'),       # fallback bezpieczny
    ('', 'jak-nowy'),                  # empty
    ('cos-nieznanego', 'jak-nowy'),    # unknown → safe default
])
def test_norm_stan(raw, expected):
    assert _norm_stan(raw) == expected


# ──────────────────────────────────────────────────────────────────────────────
# _norm_kategoria — Hub kategoria → WC product_cat slugs
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    ('audio', ['audio']),
    ('AGD', ['wnetrze']),
    ('Wnętrze', ['wnetrze']),
    ('narzędzia', ['narzedzia']),
    ('Narzedzia', ['narzedzia']),
    ('Elektronika', ['elektronika']),
    ('inne', []),         # nie w mapie → puste = WC default
    ('', []),
    ('xyz', []),
])
def test_norm_kategoria(raw, expected):
    assert _norm_kategoria(raw) == expected


# ──────────────────────────────────────────────────────────────────────────────
# _build_sku — EAN-{ean} jeśli walidny, inaczej HUB-{id}
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('hub_id,ean,expected', [
    (1, '5901234123457', 'EAN-5901234123457'),  # walidny EAN-13
    (1, '12345678', 'EAN-12345678'),            # EAN-8 walidny
    (1, '1234', 'HUB-1'),                       # za krótki — fallback
    (1, '', 'HUB-1'),                            # brak EAN
    (1, '1234567890ABC', 'HUB-1'),               # zawiera litery — fallback (EAN to tylko cyfry)
    (42, None, 'HUB-42'),                        # None EAN
    (999, ' 5901234123457 ', 'EAN-5901234123457'),  # whitespace strip
])
def test_build_sku(hub_id, ean, expected):
    assert _build_sku(hub_id, ean) == expected


# ──────────────────────────────────────────────────────────────────────────────
# map_hub_to_plugin — pełny mapping
# ──────────────────────────────────────────────────────────────────────────────

def _hub_row(**overrides):
    """Helper: produce Hub `produkty` row dict z domyślnymi wartościami."""
    defaults = {
        'id': 1,
        'ean': '5901234123457',
        'asin': '',
        'nazwa': 'Słuchawki bezprzewodowe ABC',
        'krotki_tytul': 'Sony WH-1000XM4',
        'opis_ai': 'Premium słuchawki z noise cancelling.',
        'ilosc': 1,
        'cena_netto': 800.0,
        'cena_brutto': 984.0,
        'cena_allegro': 1100.0,
        'lokalizacja': 'A1',
        'regal': 'A',
        'paleta_id': 5,
        'paleta': 'Pal-2026-001',
        'dostawca': 'Amazon DE',
        'kategoria': 'audio',
        'zdjecie_url': 'https://hub.local/photos/1.jpg',
        'stan': 'Jak nowy',
        'status': 'magazyn',
    }
    defaults.update(overrides)
    return defaults


def test_map_full_row():
    payload = map_hub_to_plugin(_hub_row())
    assert payload['sku'] == 'EAN-5901234123457'
    assert payload['title'] == 'Sony WH-1000XM4'  # krotki_tytul ma pierwszeństwo
    # cena_allegro (1100, RRP) preferowana; cena_brutto (984, koszt proporcjonalny) IGNOROWANE
    assert payload['price_pln'] == 1100.0
    assert payload['condition'] == 'jak-nowy'
    assert payload['stock'] == 1
    # description_html (plugin KONTRAKT: sanitize_payload czyta description_html, nie description)
    assert payload['description_html'] == 'Premium słuchawki z noise cancelling.'
    assert 'description' not in payload  # stary klucz NIE wysyłany
    assert payload['categories'] == ['audio']
    assert payload['brand'] == 'Amazon DE'
    assert payload['ean'] == '5901234123457'
    assert payload['images'] == [
        {'url': 'https://hub.local/photos/1.jpg', 'alt': 'Sony WH-1000XM4', 'is_primary': True}
    ]


def test_map_price_ignores_cena_brutto():
    """REGRESSION: cena_brutto = proporcjonalny koszt zakupu (NIE retail!).
    Nawet gdy cena_brutto > 0, ma być ignorowana.
    """
    # cena_allegro=0 + cena_brutto=500 (np. koszt z palety) + cena_netto=0 → price=0 (validation fail).
    payload = map_hub_to_plugin(_hub_row(cena_brutto=500.0, cena_allegro=0, cena_netto=0))
    assert payload['price_pln'] == 0  # cena_brutto NIE używamy jako retail


def test_map_fallback_price_to_netto_vat():
    """Brak cena_allegro — przelicz z cena_netto * 1.23 (fallback supplier retail)."""
    payload = map_hub_to_plugin(_hub_row(cena_allegro=0, cena_netto=100.0))
    assert payload['price_pln'] == 123.0  # 100 * 1.23


def test_map_fallback_title_to_nazwa():
    """Brak krotki_tytul → użyj nazwa."""
    payload = map_hub_to_plugin(_hub_row(krotki_tytul='', nazwa='Produkt długa nazwa'))
    assert payload['title'] == 'Produkt długa nazwa'


def test_map_no_ean_uses_hub_id():
    payload = map_hub_to_plugin(_hub_row(id=42, ean=''))
    assert payload['sku'] == 'HUB-42'
    assert 'ean' not in payload   # nie powinien być wysłany pusty


def test_map_unknown_kategoria_omitted():
    """Nieznana kategoria → 'categories' nie w payload."""
    payload = map_hub_to_plugin(_hub_row(kategoria='inne'))
    assert 'categories' not in payload


def test_map_stan_uszkodzony():
    payload = map_hub_to_plugin(_hub_row(stan='Uszkodzony'))
    assert payload['condition'] == 'slady-uzywania'


def test_map_no_image_no_images_key():
    payload = map_hub_to_plugin(_hub_row(zdjecie_url=''))
    assert 'images' not in payload


def test_map_multi_images_from_produkty_images_json():
    """produkty.images JSON array → wszystkie zdjęcia w payload (max 8), zdjecie_url ignored gdy są images."""
    row = _hub_row(
        zdjecie_url='https://hub.local/photos/cover.jpg',  # ignorowane gdy images są pełne
        images=json.dumps([
            'https://amazon.com/img1.jpg',
            'https://amazon.com/img2.jpg',
            'https://amazon.com/img3.jpg',
        ]),
    )
    payload = map_hub_to_plugin(row)
    assert len(payload['images']) == 3
    urls = [img['url'] for img in payload['images']]
    assert urls == [
        'https://amazon.com/img1.jpg',
        'https://amazon.com/img2.jpg',
        'https://amazon.com/img3.jpg',
    ]
    assert payload['images'][0]['is_primary'] is True
    assert payload['images'][1]['is_primary'] is False


def test_map_images_dedup():
    """Powtórzony URL → de-dup (pierwsze wystąpienie wygrywa)."""
    row = _hub_row(
        zdjecie_url='https://amazon.com/img1.jpg',  # dup z images[0]
        images=json.dumps(['https://amazon.com/img1.jpg', 'https://amazon.com/img2.jpg']),
    )
    payload = map_hub_to_plugin(row)
    assert len(payload['images']) == 2  # nie 3


def test_map_images_max_8_cap():
    """Hub może mieć więcej niż 8 zdjęć → cap na 8 (limit WC/plugin)."""
    row = _hub_row(
        images=json.dumps([f'https://amazon.com/img{i}.jpg' for i in range(12)]),
    )
    payload = map_hub_to_plugin(row)
    assert len(payload['images']) == 8


def test_map_images_invalid_json_falls_back_to_zdjecie_url():
    """Gdy images JSON corrupted → fallback do zdjecie_url."""
    row = _hub_row(
        zdjecie_url='https://hub.local/fallback.jpg',
        images='{not valid json',
    )
    payload = map_hub_to_plugin(row)
    assert payload['images'] == [
        {'url': 'https://hub.local/fallback.jpg', 'alt': 'Sony WH-1000XM4', 'is_primary': True}
    ]


def test_map_images_skips_non_http_urls():
    """data: URI, relative paths, etc → odrzucane (tylko http(s))."""
    row = _hub_row(
        images=json.dumps([
            'data:image/png;base64,iVBOR...',
            '/relative/path.jpg',
            'https://amazon.com/valid.jpg',
        ]),
    )
    payload = map_hub_to_plugin(row)
    assert len(payload['images']) == 1
    assert payload['images'][0]['url'] == 'https://amazon.com/valid.jpg'


def test_map_no_gpsr_default_no_key():
    """Bez gpsr arg → payload nie ma 'gpsr' key (produkt → draft po stronie pluginu)."""
    payload = map_hub_to_plugin(_hub_row())
    assert 'gpsr' not in payload


def test_map_with_gpsr_compliant():
    """gpsr={manufacturer_name} → trafia do payload (plugin GPSR gate → publish)."""
    gpsr = {
        'manufacturer_name': 'Test Mf',
        'manufacturer_address': 'Test Addr',
        'responsible_person_name': '',
        'responsible_person_address': '',
        'responsible_person_email': '',
        'product_safety_info': '',
    }
    payload = map_hub_to_plugin(_hub_row(), gpsr=gpsr)
    assert payload.get('gpsr') == gpsr


def test_map_with_empty_gpsr_no_key():
    """gpsr przekazane ale puste (bez manufacturer/rp) → nie idzie w payload."""
    gpsr = {
        'manufacturer_name': '',
        'responsible_person_name': '',
        'manufacturer_address': '',
        'responsible_person_address': '',
        'responsible_person_email': '',
        'product_safety_info': '',
    }
    payload = map_hub_to_plugin(_hub_row(), gpsr=gpsr)
    assert 'gpsr' not in payload


def test_map_with_responsible_person_only():
    """Sam responsible_person (bez manufacturer) → też compliant, w payload."""
    gpsr = {'responsible_person_name': 'EU Rep', 'manufacturer_name': ''}
    payload = map_hub_to_plugin(_hub_row(), gpsr=gpsr)
    assert payload.get('gpsr', {}).get('responsible_person_name') == 'EU Rep'


# ──────────────────────────────────────────────────────────────────────────────
# validate_payload — happy + each failure mode
# ──────────────────────────────────────────────────────────────────────────────

def test_validate_ok():
    payload = map_hub_to_plugin(_hub_row())
    ok, err = validate_payload(payload)
    assert ok is True
    assert err is None


def test_validate_bad_sku():
    payload = map_hub_to_plugin(_hub_row())
    payload['sku'] = 'lowercase-not-allowed'
    ok, err = validate_payload(payload)
    assert ok is False
    assert 'sku' in err


def test_validate_missing_title():
    payload = map_hub_to_plugin(_hub_row(krotki_tytul='', nazwa=''))
    ok, err = validate_payload(payload)
    assert ok is False
    assert 'title' in err


def test_validate_zero_price():
    payload = map_hub_to_plugin(_hub_row(cena_allegro=0, cena_netto=0))
    ok, err = validate_payload(payload)
    assert ok is False
    assert 'price' in err


def test_validate_bad_condition():
    payload = map_hub_to_plugin(_hub_row())
    payload['condition'] = 'cos-nielegalnego'
    ok, err = validate_payload(payload)
    assert ok is False
    assert 'condition' in err


# ──────────────────────────────────────────────────────────────────────────────
# push_product — HMAC signing + HTTP POST (mocked)
# ──────────────────────────────────────────────────────────────────────────────

def test_push_product_signs_canonical_string():
    """Verify że POST używa correct headers + canonical string podpisany TYM samym algorytmem co plugin."""
    payload = {'sku': 'EAN-5901234123457', 'title': 'X', 'price_pln': 100.0, 'condition': 'nowy', 'stock': 1}
    secret = 'a' * 64

    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured['url'] = url
        captured['data'] = data
        captured['headers'] = headers
        m = MagicMock()
        m.status_code = 201
        m.json.return_value = {'wc_product_id': 999}
        m.text = json.dumps({'wc_product_id': 999})
        return m

    with patch('modules.sklepakces_push.requests.post', side_effect=fake_post):
        status, response = push_product(payload, url='https://example.test', secret=secret)

    assert status == 201
    assert response['wc_product_id'] == 999
    # POSTujemy na ENDPOINT_URL_PATH (z /wp-json), ale podpisujemy ENDPOINT_CANONICAL_PATH (bez /wp-json).
    assert captured['url'] == 'https://example.test' + ENDPOINT_URL_PATH
    h = captured['headers']
    assert h['Content-Type'] == 'application/json'
    assert h['X-Akces-Timestamp']
    assert h['X-Akces-Signature']
    assert h['X-Akces-Nonce']

    # Verify signature matches canonical (same algo as plugin PHP).
    # KRYTYCZNE: canonical path = ENDPOINT_CANONICAL_PATH ('/akces/v1/products'),
    # nie ENDPOINT_URL_PATH (WP REST router strippuje '/wp-json' przed routingiem).
    import hashlib
    import hmac as _hmac
    body = captured['data'].decode('utf-8') if isinstance(captured['data'], bytes) else captured['data']
    canonical = f'POST:{ENDPOINT_CANONICAL_PATH}:{h["X-Akces-Timestamp"]}:{body}'
    expected = _hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    assert h['X-Akces-Signature'] == expected


def test_push_product_raises_without_url(monkeypatch):
    monkeypatch.setattr('modules.sklepakces_push.get_sklepakces_url', lambda: '')
    monkeypatch.setattr('modules.sklepakces_push.get_hmac_secret', lambda: 'x' * 64)
    with pytest.raises(RuntimeError, match='sklepakces_url'):
        push_product({'sku': 'X', 'title': 'X', 'price_pln': 1, 'condition': 'nowy', 'stock': 1})


def test_push_product_raises_without_secret(monkeypatch):
    monkeypatch.setattr('modules.sklepakces_push.get_sklepakces_url', lambda: 'https://example.test')
    monkeypatch.setattr('modules.sklepakces_push.get_hmac_secret', lambda: '')
    with pytest.raises(RuntimeError, match='sklepakces_hmac_secret'):
        push_product({'sku': 'X', 'title': 'X', 'price_pln': 1, 'condition': 'nowy', 'stock': 1})


def test_push_product_network_error_returns_zero():
    """RequestException → (0, {'error': ...})"""
    import requests
    with patch('modules.sklepakces_push.requests.post', side_effect=requests.ConnectionError('boom')):
        status, response = push_product(
            {'sku': 'X', 'title': 'X', 'price_pln': 1, 'condition': 'nowy', 'stock': 1},
            url='https://example.test', secret='a' * 64,
        )
    assert status == 0
    assert 'error' in response


# ──────────────────────────────────────────────────────────────────────────────
# Idempotency via mirror table
# ──────────────────────────────────────────────────────────────────────────────

def _mirror_conn(tmp_path):
    """Tworzy in-memory SQLite z sklepakces_products + sklepakces_webhook_log schema."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE sklepakces_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wc_product_id INTEGER NOT NULL UNIQUE,
        sku TEXT,
        name TEXT NOT NULL,
        regular_price REAL,
        sale_price REAL,
        stock_quantity INTEGER DEFAULT 0,
        product_data TEXT NOT NULL,
        gpsr_data TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )''')
    conn.execute('''CREATE TABLE sklepakces_webhook_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        wc_order_id INTEGER,
        status TEXT NOT NULL,
        http_code INTEGER NOT NULL,
        error_message TEXT,
        duration_ms INTEGER,
        client_ip TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    return conn


def test_already_synced_false_for_new_sku(tmp_path):
    conn = _mirror_conn(tmp_path)
    assert already_synced(conn, 'EAN-5901234123457') is False


def test_already_synced_true_after_record(tmp_path):
    conn = _mirror_conn(tmp_path)
    payload = {'sku': 'EAN-5901234123457', 'title': 'X', 'price_pln': 100.0, 'stock': 1}
    record_sync(conn, payload, wc_product_id=999, success=True)
    assert already_synced(conn, 'EAN-5901234123457') is True


def test_record_sync_skipped_when_fail(tmp_path):
    """Fail (success=False lub wc_product_id=None) → nie zaśmieca mirror."""
    conn = _mirror_conn(tmp_path)
    payload = {'sku': 'EAN-FAIL', 'title': 'X', 'price_pln': 100.0, 'stock': 1}
    record_sync(conn, payload, wc_product_id=None, success=False)
    record_sync(conn, payload, wc_product_id=999, success=False)
    assert already_synced(conn, 'EAN-FAIL') is False


def test_record_sync_upsert_on_wc_id(tmp_path):
    """Drugi push tego samego wc_product_id → UPDATE (nie duplikat)."""
    conn = _mirror_conn(tmp_path)
    payload_v1 = {'sku': 'EAN-1', 'title': 'v1', 'price_pln': 100.0, 'stock': 1}
    payload_v2 = {'sku': 'EAN-1', 'title': 'v2', 'price_pln': 200.0, 'stock': 5}
    record_sync(conn, payload_v1, wc_product_id=42, success=True)
    record_sync(conn, payload_v2, wc_product_id=42, success=True)
    rows = conn.execute('SELECT * FROM sklepakces_products WHERE wc_product_id = 42').fetchall()
    assert len(rows) == 1
    assert rows[0]['name'] == 'v2'
    assert float(rows[0]['regular_price']) == 200.0
    assert int(rows[0]['stock_quantity']) == 5


def test_record_log_writes_audit_entry(tmp_path):
    conn = _mirror_conn(tmp_path)
    record_log(conn, 'EAN-1', 201, 'success', None, 123)
    record_log(conn, 'EAN-2', 422, 'error', 'invalid sku', 50)
    rows = conn.execute('SELECT * FROM sklepakces_webhook_log ORDER BY id').fetchall()
    assert len(rows) == 2
    assert rows[0]['event_type'] == 'product_push'
    assert rows[0]['status'] == 'success'
    assert rows[0]['http_code'] == 201
    assert rows[1]['status'] == 'error'
    assert rows[1]['error_message'] == 'invalid sku'
