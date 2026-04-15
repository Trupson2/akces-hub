"""Testy /api/v1/pallets."""
import json
import uuid


def test_list_pallets(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.get('/api/v1/pallets?per_page=5', headers=auth_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'success'
    assert isinstance(body['data'], list)


def test_create_pallet_minimal(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/pallets',
                    data=json.dumps({
                        'name': f'Test pallet {uuid.uuid4().hex[:6]}',
                        'supplier': 'Amazon DE',
                        'purchase_price': 1500.0,
                        'product_count': 50,
                    }),
                    headers=auth_headers)
    assert r.status_code == 201
    data = r.get_json()['data']
    assert data['supplier'] == 'Amazon DE'
    assert data['purchase_price'] == 1500.0
    pid = data['id']

    r = client.get(f'/api/v1/pallets/{pid}', headers=auth_headers)
    assert r.status_code == 200
