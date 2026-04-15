"""Testy /api/v1/products."""
import json
import uuid


def _unique_name(prefix='test-product'):
    return f'{prefix}-{uuid.uuid4().hex[:8]}'


def test_list_products_empty_or_paginated(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.get('/api/v1/products?per_page=5', headers=auth_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'success'
    assert isinstance(body['data'], list)
    assert body['meta']['per_page'] == 5
    assert body['meta']['page'] == 1
    assert 'total' in body['meta']


def test_create_product_and_get(api_client, auth_headers):
    client, _plain, _kid = api_client
    name = _unique_name()
    r = client.post('/api/v1/products',
                    data=json.dumps({'name': name, 'price_net': 100.0, 'price_gross': 123.0, 'stock': 5}),
                    headers=auth_headers)
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body['status'] == 'success'
    pid = body['data']['id']
    assert body['data']['name'] == name
    assert body['data']['price']['net'] == 100.0
    assert body['data']['stock'] == 5

    # GET by id
    r = client.get(f'/api/v1/products/{pid}', headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['data']['id'] == pid


def test_create_product_missing_name_returns_validation_error(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/products',
                    data=json.dumps({'price_net': 10.0}),
                    headers=auth_headers)
    assert r.status_code == 400
    body = r.get_json()
    assert body['code'] == 'VALIDATION_ERROR'
    assert 'name' in body['details']


def test_update_product_partial(api_client, auth_headers):
    client, _plain, _kid = api_client
    name = _unique_name()
    r = client.post('/api/v1/products',
                    data=json.dumps({'name': name, 'stock': 10}),
                    headers=auth_headers)
    pid = r.get_json()['data']['id']

    r = client.put(f'/api/v1/products/{pid}',
                   data=json.dumps({'stock': 42, 'category': 'laptopy'}),
                   headers=auth_headers)
    assert r.status_code == 200
    data = r.get_json()['data']
    assert data['stock'] == 42
    assert data['category'] == 'laptopy'
    assert data['name'] == name  # nie zmienilo sie


def test_delete_product_soft(api_client, auth_headers):
    client, _plain, _kid = api_client
    name = _unique_name()
    r = client.post('/api/v1/products',
                    data=json.dumps({'name': name}), headers=auth_headers)
    pid = r.get_json()['data']['id']
    r = client.delete(f'/api/v1/products/{pid}', headers=auth_headers)
    assert r.status_code == 200
    # Powinno pozostac w DB ale ze status=deleted
    r = client.get(f'/api/v1/products/{pid}', headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['data']['status'] == 'deleted'


def test_get_nonexistent_product_404(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.get('/api/v1/products/999999999', headers=auth_headers)
    assert r.status_code == 404
    assert r.get_json()['code'] == 'NOT_FOUND'


def test_list_products_pagination(api_client, auth_headers):
    client, _plain, _kid = api_client
    # Stworz kilka produktow zeby wymusic pagination
    names = []
    for _ in range(3):
        n = _unique_name()
        names.append(n)
        client.post('/api/v1/products',
                    data=json.dumps({'name': n}), headers=auth_headers)
    r = client.get('/api/v1/products?per_page=1&page=1', headers=auth_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert len(body['data']) == 1
    assert body['meta']['total_pages'] >= 3
    assert body['meta']['per_page'] == 1


def test_filter_products_by_search(api_client, auth_headers):
    client, _plain, _kid = api_client
    uniq = uuid.uuid4().hex[:12]
    name = f'Dell Latitude {uniq}'
    client.post('/api/v1/products',
                data=json.dumps({'name': name}), headers=auth_headers)
    r = client.get(f'/api/v1/products?search={uniq}', headers=auth_headers)
    assert r.status_code == 200
    data = r.get_json()['data']
    assert any(p['name'] == name for p in data)


def test_product_stock_endpoint(api_client, auth_headers):
    client, _plain, _kid = api_client
    name = _unique_name()
    r = client.post('/api/v1/products',
                    data=json.dumps({'name': name, 'stock': 7}),
                    headers=auth_headers)
    pid = r.get_json()['data']['id']
    r = client.get(f'/api/v1/products/{pid}/stock', headers=auth_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body['data']['product_id'] == pid
    assert body['data']['stock'] == 7
