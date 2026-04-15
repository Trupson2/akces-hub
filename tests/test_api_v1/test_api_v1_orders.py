"""Testy /api/v1/orders."""
import json
import uuid


def test_list_orders(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.get('/api/v1/orders?per_page=10', headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['status'] == 'success'


def test_create_order_minimal(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/orders',
                    data=json.dumps({
                        'product_name': f'Test order {uuid.uuid4().hex[:6]}',
                        'price': 299.0,
                        'buyer': 'Jan Kowalski',
                    }),
                    headers=auth_headers)
    assert r.status_code == 201
    data = r.get_json()['data']
    assert data['price'] == 299.0
    assert data['buyer'] == 'Jan Kowalski'
    assert data['status'] == 'nowa'


def test_create_order_missing_price_validation(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/orders',
                    data=json.dumps({'product_name': 'X'}),
                    headers=auth_headers)
    assert r.status_code == 400
    assert 'price' in r.get_json()['details']


def test_create_order_triggers_webhook_delivery_queue(api_client, auth_headers):
    """Po create + zarejestrowanym webhooku powinien pojawic sie delivery row pending."""
    client, _plain, _kid = api_client
    # Zarejestruj webhook dla order.created
    r = client.post('/api/v1/webhooks',
                    data=json.dumps({
                        'url': 'https://example.com/hook',
                        'events': ['order.created'],
                    }),
                    headers=auth_headers)
    assert r.status_code == 201

    # Stworz order
    r = client.post('/api/v1/orders',
                    data=json.dumps({
                        'product_name': 'Hook-test',
                        'price': 1.0,
                    }),
                    headers=auth_headers)
    assert r.status_code == 201

    # Sprawdz webhook_deliveries
    from modules.database import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM webhook_deliveries WHERE event_type = 'order.created'"
    ).fetchall()
    assert len(rows) >= 1
    payload = json.loads(rows[0]['payload'])
    assert payload['product_name'] == 'Hook-test'


def test_update_order_status(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/orders',
                    data=json.dumps({'price': 10.0, 'product_name': 'x'}),
                    headers=auth_headers)
    oid = r.get_json()['data']['id']

    r = client.put(f'/api/v1/orders/{oid}/status',
                   data=json.dumps({'status': 'wyslana'}),
                   headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()['data']['status'] == 'wyslana'


def test_cancel_order(api_client, auth_headers):
    client, _plain, _kid = api_client
    r = client.post('/api/v1/orders',
                    data=json.dumps({'price': 10.0}), headers=auth_headers)
    oid = r.get_json()['data']['id']
    r = client.delete(f'/api/v1/orders/{oid}', headers=auth_headers)
    assert r.status_code == 200
    # Sprawdz ze status zmienil sie
    r = client.get(f'/api/v1/orders/{oid}', headers=auth_headers)
    assert r.get_json()['data']['status'] == 'anulowana'
