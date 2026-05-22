"""Tests for modules.amazon_gpsr_scraper.

Pokrywa:
- parse GPSR z HTML mock'ów (z danymi, bez danych, multi-region)
- captcha detection
- cache roundtrip (lookup/save/upsert)
- fallback (no asin / no GPSR data → AKCES jako importer)
- helper functions (_strip_html, _parse_address_lines)
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from modules.amazon_gpsr_scraper import (
    GpsrData,
    FALLBACK_RESPONSIBLE_PERSON,
    _strip_html,
    _parse_address_lines,
    is_captcha_page,
    parse_gpsr_from_html,
    cache_lookup,
    cache_save,
    init_cache_schema,
    fetch_gpsr,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def test_strip_html_basic():
    html = '<p>Foo <strong>bar</strong></p>\n<p>baz</p>'
    out = _strip_html(html)
    assert 'Foo bar' in out
    assert 'baz' in out
    assert '<' not in out


def test_strip_html_drops_script_and_style():
    html = 'A<script>alert(1)</script>B<style>.x{color:red}</style>C'
    assert _strip_html(html) == 'A B C'.replace('  ', ' ').strip() or _strip_html(html).replace('  ', ' ') == 'A B C'


def test_strip_html_br_to_newline():
    html = 'line1<br>line2<br/>line3'
    out = _strip_html(html)
    assert 'line1' in out and 'line2' in out and 'line3' in out


def test_parse_address_lines_name_addr_email():
    text = 'Jjc Photo Equipment Co., Ltd.\n123 Industrial Park, Shenzhen, China\ncontact@jjc.com'
    name, addr, email = _parse_address_lines(text)
    assert name == 'Jjc Photo Equipment Co., Ltd.'
    assert '123 Industrial Park' in addr
    assert 'Shenzhen' in addr
    assert email == 'contact@jjc.com'


def test_parse_address_lines_empty():
    assert _parse_address_lines('') == ('', '', '')


def test_parse_address_lines_only_email():
    text = 'contact@xyz.com'
    name, addr, email = _parse_address_lines(text)
    assert name == 'contact@xyz.com'  # nazwa = pierwsza linia w fallback
    assert email == 'contact@xyz.com'


# ──────────────────────────────────────────────────────────────────────────────
# Captcha detection
# ──────────────────────────────────────────────────────────────────────────────

def test_is_captcha_page_detects_validate():
    assert is_captcha_page('<html>/errors/validateCaptcha</html>')
    assert is_captcha_page('Type the characters you see in this image')
    assert is_captcha_page('Geben Sie die Zeichen ein')
    assert not is_captcha_page('<html><body>Normal product page</body></html>')


# ──────────────────────────────────────────────────────────────────────────────
# Parser — GPSR extraction z mock HTML
# ──────────────────────────────────────────────────────────────────────────────

HTML_WITH_RP_AND_MF = '''
<html><body>
  <div id="product-safety-and-compliance_feature_div">
    <h4>Responsible Person</h4>
    <p>My EU Rep GmbH<br>
       Berliner Str. 1, 10001 Berlin, Germany<br>
       eu-rep@example.com</p>
    <h4>Manufacturer</h4>
    <p>Jjc Photo Equipment Co., Ltd.<br>
       123 Industrial Park, Shenzhen, China</p>
  </div>
</body></html>
'''


def test_parse_extracts_responsible_person():
    out = parse_gpsr_from_html(HTML_WITH_RP_AND_MF)
    assert out['responsible_person_name'] == 'My EU Rep GmbH'
    assert 'Berliner Str' in out['responsible_person_address']
    assert out['responsible_person_email'] == 'eu-rep@example.com'


def test_parse_extracts_manufacturer():
    out = parse_gpsr_from_html(HTML_WITH_RP_AND_MF)
    assert out['manufacturer_name'] == 'Jjc Photo Equipment Co., Ltd.'
    assert 'Shenzhen' in out['manufacturer_address']


def test_parse_safety_info_captures_whole_block():
    out = parse_gpsr_from_html(HTML_WITH_RP_AND_MF)
    assert 'My EU Rep GmbH' in out['product_safety_info']
    assert 'Jjc Photo Equipment' in out['product_safety_info']


HTML_DE_VERANTWORTLICHER = '''
<html><body>
  <section id="important-information">
    EU-Verantwortlicher: My Company GmbH, Berliner Str. 1, 10001 Berlin, support@mycompany.de
    Hersteller: Some Factory Co., Ltd., Industrial Zone, Shanghai, China
  </section>
</body></html>
'''


def test_parse_de_labels_verantwortlicher_hersteller():
    out = parse_gpsr_from_html(HTML_DE_VERANTWORTLICHER)
    assert 'My Company GmbH' in out['responsible_person_name'] + out['responsible_person_address']
    assert out['responsible_person_email'] == 'support@mycompany.de'
    assert 'Some Factory' in out['manufacturer_name'] + out['manufacturer_address']


HTML_NO_GPSR = '''
<html><body>
  <div id="productTitle">Product XYZ</div>
  <div id="feature-bullets">
    <ul><li>Feature A</li><li>Feature B</li></ul>
  </div>
</body></html>
'''


def test_parse_no_gpsr_returns_empty():
    out = parse_gpsr_from_html(HTML_NO_GPSR)
    assert out['manufacturer_name'] == ''
    assert out['responsible_person_name'] == ''
    assert out['product_safety_info'] == ''


# ──────────────────────────────────────────────────────────────────────────────
# Cache roundtrip
# ──────────────────────────────────────────────────────────────────────────────

def _cache_conn():
    """In-memory SQLite z gpsr_amazon_cache schema."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    init_cache_schema(conn)
    return conn


def test_cache_miss_returns_none():
    conn = _cache_conn()
    assert cache_lookup('B0NOTHERE', 'de', conn=conn) is None


def test_cache_save_and_lookup_roundtrip():
    conn = _cache_conn()
    g = GpsrData(
        asin='B07TEST123', region='de',
        manufacturer_name='Test Manufacturer',
        manufacturer_address='Test Address',
        responsible_person_name='Test EU Rep',
        responsible_person_email='rep@example.com',
        source='amazon',
        source_url='https://www.amazon.de/dp/B07TEST123',
    )
    cache_save(g, raw_snippet='raw html snippet here', conn=conn)
    out = cache_lookup('B07TEST123', 'de', conn=conn)
    assert out is not None
    assert out.manufacturer_name == 'Test Manufacturer'
    assert out.responsible_person_name == 'Test EU Rep'
    assert out.responsible_person_email == 'rep@example.com'
    assert out.source == 'cache'  # po loadzie zwraca jako 'cache'


def test_cache_upsert_on_conflict():
    """Drugi cache_save dla tego samego (asin, region) → UPDATE, nie duplikat."""
    conn = _cache_conn()
    cache_save(GpsrData(asin='B07X', region='de', manufacturer_name='OldName', source='amazon'), conn=conn)
    cache_save(GpsrData(asin='B07X', region='de', manufacturer_name='NewName', source='amazon'), conn=conn)
    rows = conn.execute('SELECT * FROM gpsr_amazon_cache WHERE asin = "B07X"').fetchall()
    assert len(rows) == 1
    assert rows[0]['manufacturer_name'] == 'NewName'


# ──────────────────────────────────────────────────────────────────────────────
# fetch_gpsr — fallback + cache + Amazon (mocked)
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_gpsr_no_asin_returns_fallback():
    g = fetch_gpsr(asin='', use_fallback=True, conn=_cache_conn())
    assert g.source == 'fallback'
    assert g.responsible_person_name == FALLBACK_RESPONSIBLE_PERSON['responsible_person_name']
    assert g.is_compliant()


def test_fetch_gpsr_no_asin_no_fallback_returns_empty():
    g = fetch_gpsr(asin='', use_fallback=False, conn=_cache_conn())
    assert g.source == ''
    assert not g.is_compliant()


def test_fetch_gpsr_uses_cache_when_present():
    conn = _cache_conn()
    cache_save(
        GpsrData(asin='B07CACHED', region='de', responsible_person_name='Cached Rep', source='amazon'),
        conn=conn,
    )
    with patch('modules.amazon_gpsr_scraper.fetch_amazon_html') as mock_fetch:
        g = fetch_gpsr(asin='B07CACHED', region='de', conn=conn)
        mock_fetch.assert_not_called()  # cache hit → bez HTTP
    assert g.source == 'cache'
    assert g.responsible_person_name == 'Cached Rep'


def test_fetch_gpsr_calls_amazon_when_no_cache():
    conn = _cache_conn()
    with patch('modules.amazon_gpsr_scraper.fetch_amazon_html', return_value=HTML_WITH_RP_AND_MF):
        g = fetch_gpsr(asin='B07NEW', region='de', conn=conn)
    assert g.source == 'amazon'
    assert g.responsible_person_name == 'My EU Rep GmbH'
    # Cache saved → drugi lookup z cache
    g2 = cache_lookup('B07NEW', 'de', conn=conn)
    assert g2 is not None
    assert g2.responsible_person_name == 'My EU Rep GmbH'


def test_fetch_gpsr_fallback_when_amazon_no_data():
    """Amazon HTML bez GPSR fields → fallback (AKCES jako importer)."""
    conn = _cache_conn()
    with patch('modules.amazon_gpsr_scraper.fetch_amazon_html', return_value=HTML_NO_GPSR):
        g = fetch_gpsr(asin='B07EMPTY', region='de', use_fallback=True, conn=conn)
    assert g.source == 'fallback'
    assert g.responsible_person_name == FALLBACK_RESPONSIBLE_PERSON['responsible_person_name']
    assert g.is_compliant()


def test_fetch_gpsr_fallback_when_amazon_fail():
    """Amazon zwraca None (captcha/503) → fallback."""
    conn = _cache_conn()
    with patch('modules.amazon_gpsr_scraper.fetch_amazon_html', return_value=None):
        g = fetch_gpsr(asin='B07FAIL', region='de', use_fallback=True, conn=conn)
    assert g.source == 'fallback'
    assert g.is_compliant()


# ──────────────────────────────────────────────────────────────────────────────
# GpsrData.to_plugin_payload — format zgodny z plugin sanitize_gpsr
# ──────────────────────────────────────────────────────────────────────────────

def test_to_plugin_payload_shape():
    g = GpsrData(
        asin='B07X', region='de',
        manufacturer_name='Mf',
        manufacturer_address='Addr1',
        responsible_person_name='Rep',
        responsible_person_address='Addr2',
        responsible_person_email='r@x.com',
        product_safety_info='Safety bullets',
        source='amazon',
    )
    p = g.to_plugin_payload()
    assert set(p.keys()) == {
        'manufacturer_name', 'manufacturer_address',
        'responsible_person_name', 'responsible_person_address', 'responsible_person_email',
        'product_safety_info',
    }
    assert p['manufacturer_name'] == 'Mf'
    assert p['responsible_person_email'] == 'r@x.com'
    # NIE wycieka source/asin/region (plugin schema ich nie wymaga, sanitize_gpsr by je zignorował)


def test_is_compliant_logic():
    assert GpsrData(manufacturer_name='X').is_compliant() is True
    assert GpsrData(responsible_person_name='Y').is_compliant() is True
    assert GpsrData().is_compliant() is False
