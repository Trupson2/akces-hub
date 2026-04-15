"""Testy OpenAPI spec + Swagger UI docs."""


def test_openapi_spec_accessible_public(api_client):
    """GET /api/v1/openapi.json jest publiczne (bez auth)."""
    client, _plain, _kid = api_client
    r = client.get('/api/v1/openapi.json')
    assert r.status_code == 200
    spec = r.get_json()
    assert spec['openapi'].startswith('3.0')
    assert spec['info']['title'].lower().startswith('akces')
    # Sanity: major endpoints obecne
    assert '/products' in spec['paths']
    assert '/orders' in spec['paths']
    assert '/webhooks' in spec['paths']
    assert '/health' in spec['paths']


def test_swagger_ui_html_served(api_client):
    """GET /api/v1/docs zwraca HTML ze Swagger UI."""
    client, _plain, _kid = api_client
    r = client.get('/api/v1/docs')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'swagger-ui' in body.lower()
    assert '/api/v1/openapi.json' in body
