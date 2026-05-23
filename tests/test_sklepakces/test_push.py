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
    ('AGD', ['agd-male', 'wnetrze']),       # legacy 'agd' → specific + hero tile
    ('Wnętrze', ['wnetrze']),
    ('narzędzia', ['narzedzia']),
    ('Narzedzia', ['narzedzia']),
    ('Elektronika', ['elektronika']),
    ('inne', []),         # NOT mapped → puste = WC default
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


# ──────────────────────────────────────────────────────────────────────────────
# Allegro active offer price source + suspicious low-price detection
# ──────────────────────────────────────────────────────────────────────────────

from modules.sklepakces_push import (  # noqa: E402
    _get_allegro_active_price,
    _get_allegro_active_offer,
    _paleta_koszt_szt,
    SUSPICIOUS_MARKUP_THRESHOLD,
)


def _oferty_conn(tmp_path):
    """In-memory DB z oferty table (minimal schema dla testów)."""
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE oferty (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        allegro_id TEXT,
        produkt_id INTEGER,
        tytul TEXT,
        opis TEXT,
        cena REAL DEFAULT 0,
        ilosc INTEGER DEFAULT 1,
        status TEXT DEFAULT 'draft',
        data_aktualizacji TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    return db


def test_get_allegro_active_price_finds_aktywna(tmp_path):
    conn = _oferty_conn(tmp_path)
    conn.execute(
        "INSERT INTO oferty (produkt_id, tytul, cena, status) VALUES (13, 'Test', 250.0, 'aktywna')"
    )
    assert _get_allegro_active_price(conn, 13) == 250.0


def test_get_allegro_active_price_ignores_draft_and_ended(tmp_path):
    conn = _oferty_conn(tmp_path)
    conn.execute("INSERT INTO oferty (produkt_id, tytul, cena, status) VALUES (13, 'Draft', 100.0, 'draft')")
    conn.execute("INSERT INTO oferty (produkt_id, tytul, cena, status) VALUES (13, 'Ended', 200.0, 'zakonczona')")
    assert _get_allegro_active_price(conn, 13) is None


def test_get_allegro_active_price_picks_latest_when_multiple_active(tmp_path):
    conn = _oferty_conn(tmp_path)
    conn.execute(
        "INSERT INTO oferty (produkt_id, tytul, cena, status, data_aktualizacji) "
        "VALUES (13, 'Old', 200.0, 'aktywna', '2026-01-01 10:00:00')"
    )
    conn.execute(
        "INSERT INTO oferty (produkt_id, tytul, cena, status, data_aktualizacji) "
        "VALUES (13, 'New', 350.0, 'aktywna', '2026-05-22 14:00:00')"
    )
    assert _get_allegro_active_price(conn, 13) == 350.0  # newest wins


def test_get_allegro_active_price_zero_cena_skipped(tmp_path):
    """sanity: oferta z cena=0 nie używana (mimo status=aktywna)."""
    conn = _oferty_conn(tmp_path)
    conn.execute("INSERT INTO oferty (produkt_id, tytul, cena, status) VALUES (13, 'Free', 0, 'aktywna')")
    assert _get_allegro_active_price(conn, 13) is None


def test_get_allegro_active_price_no_conn_returns_none():
    assert _get_allegro_active_price(None, 13) is None


def test_get_allegro_active_price_zero_hub_id_returns_none(tmp_path):
    conn = _oferty_conn(tmp_path)
    assert _get_allegro_active_price(conn, 0) is None


def test_paleta_koszt_szt_basic():
    row = {'cena_brutto': 100.0, 'ilosc': 5}
    assert _paleta_koszt_szt(row) == 20.0


def test_paleta_koszt_szt_zero_ilosc_returns_zero():
    row = {'cena_brutto': 100.0, 'ilosc': 0}
    assert _paleta_koszt_szt(row) == 0.0


def test_paleta_koszt_szt_zero_brutto_returns_zero():
    row = {'cena_brutto': 0.0, 'ilosc': 5}
    assert _paleta_koszt_szt(row) == 0.0


def test_paleta_koszt_szt_missing_fields_returns_zero():
    assert _paleta_koszt_szt({}) == 0.0


def test_map_uses_allegro_active_price_when_provided():
    """allegro_active_price NADPISUJE cena_allegro z DB (nawet jak ta jest niska)."""
    row = _hub_row(cena_allegro=40.88)  # niska cena DB
    payload = map_hub_to_plugin(row, allegro_active_price=350.0)  # realna z Allegro
    assert payload['price_pln'] == 350.0  # nadpisała


def test_map_falls_back_to_db_when_no_allegro_active_price():
    """Brak allegro_active_price → użyj cena_allegro z DB."""
    row = _hub_row(cena_allegro=1100.0)
    payload = map_hub_to_plugin(row)  # bez allegro_active_price
    assert payload['price_pln'] == 1100.0  # z DB


def test_map_zero_allegro_active_price_treated_as_missing():
    """allegro_active_price=0 → fallback (gdyby ktoś przekazał 0)."""
    row = _hub_row(cena_allegro=500.0)
    payload = map_hub_to_plugin(row, allegro_active_price=0.0)
    assert payload['price_pln'] == 500.0  # fallback do DB


def test_suspicious_threshold_value():
    """SUSPICIOUS_MARKUP_THRESHOLD sanity — niech nie skoczy nagle do 5x."""
    assert 1.0 < SUSPICIOUS_MARKUP_THRESHOLD < 2.5
    # threshold=1.3 → cena/koszt < 1.3 = alert. Realistic:
    # koszt 50zł, cena 60zł, markup=1.2 → alert (poniżej 30%).
    # koszt 50zł, cena 70zł, markup=1.4 → OK.


# ──────────────────────────────────────────────────────────────────────────────
# KATEGORIA_MAP — full Hub kategoria → WC slugs mapping
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('hub_kat,expected', [
    # Audio/RTV
    ('audio',           ['audio']),
    ('car_audio',       ['car-audio', 'audio', 'motoryzacja']),
    ('rtv',             ['rtv', 'elektronika']),
    # Elektronika family (foto_video produkt → hero tile elektronika)
    ('elektronika',     ['elektronika']),
    ('foto_video',      ['foto-video', 'elektronika']),
    ('foto-video',      ['foto-video', 'elektronika']),
    ('smart_home',      ['smart-home', 'elektronika']),
    ('komputery',       ['komputery', 'elektronika']),
    ('telefony',        ['telefony', 'elektronika']),
    ('gaming',          ['gaming', 'elektronika']),
    ('druk3d',          ['druk-3d', 'elektronika']),
    # Wnętrze family
    ('wnetrze',         ['wnetrze']),
    ('wnętrze',         ['wnetrze']),  # diacritics
    ('agd',             ['agd-male', 'wnetrze']),  # legacy
    ('agd_male',        ['agd-male', 'wnetrze']),
    ('agd_duze',        ['agd-duze', 'wnetrze']),
    ('kuchnia',         ['kuchnia', 'wnetrze']),
    ('dekoracje',       ['dekoracje', 'wnetrze']),
    ('oswietlenie',     ['oswietlenie', 'wnetrze']),
    ('oświetlenie',     ['oswietlenie', 'wnetrze']),
    # Narzędzia
    ('narzedzia',       ['narzedzia']),
    ('narzędzia',       ['narzedzia']),
    ('elektronarzedzia',['elektronarzedzia', 'narzedzia']),
    # Motoryzacja
    ('motoryzacja',     ['motoryzacja']),
    ('ev_ladowarki',    ['ev-ladowarki', 'motoryzacja']),
    # Sport
    ('sport',           ['sport']),
    ('rowery',          ['rowery', 'sport']),
    ('silownia',        ['silownia', 'sport']),
    ('siłownia',        ['silownia', 'sport']),
    ('wedkarstwo',      ['wedkarstwo', 'sport']),
    # Dzieci/zwierzeta z diakrytykami
    ('zwierzeta',       ['zwierzeta']),
    ('zwierzęta',       ['zwierzeta']),
    ('niemowleta',      ['niemowleta']),
    ('niemowlęta',      ['niemowleta']),
    # Inne — wszystkie warianty puste/inne → []
    ('inne',            []),
    ('',                []),
    ('nieznana_kategoria_xyz', []),
])
def test_norm_kategoria_mapping(hub_kat, expected):
    assert _norm_kategoria(hub_kat) == expected


def test_norm_kategoria_underscore_dash_fallback():
    """Gdy klucz nie istnieje w MAP, try replace _↔- jako fallback."""
    # foto-video (z dashem) NIE jest aliasem foto_video? Sprawdzę obu:
    assert _norm_kategoria('foto-video') == _norm_kategoria('foto_video')
    # case-insensitive
    assert _norm_kategoria('FOTO_VIDEO') == _norm_kategoria('foto_video')
    assert _norm_kategoria('  Audio  ') == ['audio']  # strip + lower


def test_map_full_row_audio_kategoria_unchanged():
    """Regression: 'audio' nadal mapuje na sam 'audio' (nie dodaje innych)."""
    row = _hub_row(kategoria='audio')
    payload = map_hub_to_plugin(row)
    assert payload['categories'] == ['audio']


def test_map_foto_video_includes_hero_tile_parent():
    """foto_video produkt trafia do hero tile 'elektronika' (też do specyficznej kategorii)."""
    row = _hub_row(kategoria='foto_video')
    payload = map_hub_to_plugin(row)
    assert 'foto-video' in payload['categories']
    assert 'elektronika' in payload['categories']  # hero tile parent


# ──────────────────────────────────────────────────────────────────────────────
# Allegro active offer — STOCK (ilosc z oferty.ilosc, nie produkty.ilosc)
# ──────────────────────────────────────────────────────────────────────────────

def test_get_allegro_active_offer_returns_dict(tmp_path):
    """Pełna oferta: cena + ilosc + opis + tytul + allegro_id."""
    conn = _oferty_conn(tmp_path)
    conn.execute(
        "INSERT INTO oferty (allegro_id, produkt_id, tytul, opis, cena, ilosc, status) "
        "VALUES ('17539123456', 13, 'Test', 'Opis tutaj', 199.99, 7, 'aktywna')"
    )
    offer = _get_allegro_active_offer(conn, 13)
    assert offer == {
        'cena': 199.99, 'ilosc': 7,
        'opis': 'Opis tutaj', 'tytul': 'Test',
        'allegro_id': '17539123456',
    }


def test_get_allegro_active_offer_none_when_no_active(tmp_path):
    conn = _oferty_conn(tmp_path)
    conn.execute("INSERT INTO oferty (produkt_id, tytul, cena, ilosc, status) VALUES (13, 'X', 100, 5, 'draft')")
    assert _get_allegro_active_offer(conn, 13) is None


def test_get_allegro_active_offer_handles_zero_ilosc(tmp_path):
    """Edge: oferta aktywna ale ilosc=0 (sold out) — zwraca offer z ilosc=0,
    user może rozważyć skip albo set stock=0 w WC."""
    conn = _oferty_conn(tmp_path)
    conn.execute(
        "INSERT INTO oferty (produkt_id, tytul, cena, ilosc, status) VALUES (13, 'Sold', 100.0, 0, 'aktywna')"
    )
    offer = _get_allegro_active_offer(conn, 13)
    assert offer == {
        'cena': 100.0, 'ilosc': 0,
        'opis': '', 'tytul': 'Sold',
        'allegro_id': '',
    }


def test_get_allegro_active_price_backward_compat(tmp_path):
    """Stara funkcja nadal zwraca tylko cenę (deleguje do _offer)."""
    conn = _oferty_conn(tmp_path)
    conn.execute(
        "INSERT INTO oferty (produkt_id, tytul, cena, ilosc, status) VALUES (13, 'Test', 250.0, 3, 'aktywna')"
    )
    assert _get_allegro_active_price(conn, 13) == 250.0


def test_map_uses_allegro_active_stock_when_provided():
    """allegro_active_stock NADPISUJE produkty.ilosc."""
    row = _hub_row(ilosc=1)  # Hub ma 1 sztukę
    payload = map_hub_to_plugin(row, allegro_active_stock=7)  # Allegro auction ma 7
    assert payload['stock'] == 7


def test_map_falls_back_to_db_stock_when_no_allegro_stock():
    """Brak allegro_active_stock → użyj produkty.ilosc."""
    row = _hub_row(ilosc=5)
    payload = map_hub_to_plugin(row)  # bez allegro_active_stock
    assert payload['stock'] == 5  # z DB


def test_map_allegro_stock_zero_respected():
    """allegro_active_stock=0 (sold out na Allegro) → push z stock=0 (out of stock)."""
    row = _hub_row(ilosc=5)  # DB pokazuje 5 ale Allegro 0 (priorytet Allegro)
    payload = map_hub_to_plugin(row, allegro_active_stock=0)
    assert payload['stock'] == 0


def test_map_allegro_price_and_stock_independent():
    """Można przekazać tylko cenę albo tylko stock niezależnie."""
    row = _hub_row(ilosc=2, cena_allegro=100.0)
    # Tylko price override:
    p1 = map_hub_to_plugin(row, allegro_active_price=500.0)
    assert p1['price_pln'] == 500.0
    assert p1['stock'] == 2  # z DB
    # Tylko stock override:
    p2 = map_hub_to_plugin(row, allegro_active_stock=10)
    assert p2['price_pln'] == 100.0  # z DB
    assert p2['stock'] == 10


# ──────────────────────────────────────────────────────────────────────────────
# Allegro active offer — OPIS (description z oferty.opis, nie opis_ai)
# ──────────────────────────────────────────────────────────────────────────────

def test_get_allegro_active_offer_includes_opis(tmp_path):
    """_get_allegro_active_offer zwraca też opis z oferty.opis."""
    conn = _oferty_conn(tmp_path)
    conn.execute(
        "INSERT INTO oferty (produkt_id, tytul, opis, cena, ilosc, status) "
        "VALUES (13, 'Tytuł listingu', '<p>Pełny opis HTML produktu</p>', 250.0, 3, 'aktywna')"
    )
    offer = _get_allegro_active_offer(conn, 13)
    assert offer['opis'] == '<p>Pełny opis HTML produktu</p>'
    assert offer['tytul'] == 'Tytuł listingu'


def test_map_uses_allegro_active_description_when_provided():
    """allegro_active_description (z oferty.opis) NADPISUJE opis_ai z DB."""
    row = _hub_row(opis_ai='AI-generated z Hub')
    payload = map_hub_to_plugin(
        row, allegro_active_description='<p>Opis z Allegro listing — user-written</p>'
    )
    assert payload['description_html'] == '<p>Opis z Allegro listing — user-written</p>'


def test_map_falls_back_to_opis_ai_when_no_allegro_description():
    """Brak allegro_active_description → użyj opis_ai z Hub DB."""
    row = _hub_row(opis_ai='Hub Gemini description')
    payload = map_hub_to_plugin(row)
    assert payload['description_html'] == 'Hub Gemini description'


def test_map_empty_allegro_description_falls_back():
    """allegro_active_description='' (puste) → fallback do opis_ai (nie ustaw pustego)."""
    row = _hub_row(opis_ai='Hub fallback opis')
    payload = map_hub_to_plugin(row, allegro_active_description='')
    assert payload['description_html'] == 'Hub fallback opis'


def test_map_whitespace_allegro_description_falls_back():
    """Whitespace-only allegro opis → fallback (.strip() check)."""
    row = _hub_row(opis_ai='Hub opis')
    payload = map_hub_to_plugin(row, allegro_active_description='   \n\t  ')
    assert payload['description_html'] == 'Hub opis'


def test_map_no_description_falls_back_to_auto_generated():
    """Brak opisu w obu source'ach → auto-generuj minimalny z nazwy+stanu+marki etc.

    Chroni przed placeholder "Pełny opis... wkrótce" na sklepie.
    """
    row = _hub_row(opis_ai='')
    payload = map_hub_to_plugin(row)
    assert 'description_html' in payload
    desc = payload['description_html']
    # Powinien zawierać nazwę produktu (krotki_tytul w _hub_row)
    assert 'Sony WH-1000XM4' in desc
    # I bullet pointy z dostępnych pól
    assert 'Stan' in desc
    assert 'Kategoria' in desc or 'Marka' in desc


def test_map_no_description_no_nazwa_no_key():
    """Brak NAWET nazwy → brak description_html (bez nazwy nie generujemy)."""
    row = _hub_row(opis_ai='', nazwa='', krotki_tytul='')
    payload = map_hub_to_plugin(row)
    assert 'description_html' not in payload
