"""Testy /api/v1/stock."""
import json
import uuid


def test_stock_overview(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.get('/api/v1/stock', headers=auth_headers)
    assert r.status_code == 200
    data = r.get_json()['data']
    assert 'total_stock' in data
    assert 'by_category' in data


def test_stock_for_specific_product(api_client, auth_headers):
    client, _plain, _kid = api_client
    name = f'stock-test-{uuid.uuid4().hex[:6]}'
    r = client.post('/api/v1/products',
                    data=json.dumps({'name': name, 'stock': 11}),
                    headers=auth_headers)
    pid = r.get_json()['data']['id']
    r = client.get(f'/api/v1/stock/{pid}', headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['data']['stock'] == 11


def test_adjust_stock_positive_and_negative(api_client, auth_headers):
    client, _plain, _kid = api_client
    name = f'adjust-{uuid.uuid4().hex[:6]}'
    r = client.post('/api/v1/products',
                    data=json.dumps({'name': name, 'stock': 10}),
                    headers=auth_headers)
    pid = r.get_json()['data']['id']

    # Zmniejsz o 3
    r = client.post('/api/v1/stock/adjust',
                    data=json.dumps({'product_id': pid, 'delta': -3, 'reason': 'damaged'}),
                    headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['data']['new_stock'] == 7

    # Zwieksz o 5
    r = client.post('/api/v1/stock/adjust',
                    data=json.dumps({'product_id': pid, 'delta': 5}),
                    headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['data']['new_stock'] == 12

    # Duzo zmniejszenie — floor na 0
    r = client.post('/api/v1/stock/adjust',
                    data=json.dumps({'product_id': pid, 'delta': -1000}),
                    headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['data']['new_stock'] == 0
