"""OpenAPI 3.0 spec + Swagger UI dla API v1.

Spec generowany jako Python dict w runtime — zawsze odzwierciedla kod
(nie trzeba rebuildowac YAML przy kazdej zmianie).

Exposed:
  GET /api/v1/openapi.json — raw spec dla toolow (Postman, code-generators)
  GET /api/v1/docs         — Swagger UI HTML (uzywa swagger-ui z CDN)
"""
from __future__ import annotations

from flask import jsonify

from . import api_v1_bp


def build_spec() -> dict:
    """Buduje OpenAPI 3.0 spec dla wszystkich endpointow v1."""
    return {
        'openapi': '3.0.3',
        'info': {
            'title': 'AKCES HUB Public REST API',
            'description': (
                'Uniwersalny REST API dla integracji z instancja AKCES HUB.\n\n'
                'Autentykacja: header `X-API-Key: ak_live_...` (lub `Authorization: Bearer ...`).\n'
                'Rate limit: per-klucz (default 60 requests/min).\n'
                'Webhooki: HMAC-SHA256 signature w headerze `X-Akces-Signature`.'
            ),
            'version': '1.0.0',
            'contact': {'name': 'AKCES HUB Support'},
        },
        'servers': [
            {'url': '/api/v1', 'description': 'Current instance'},
        ],
        'components': {
            'securitySchemes': {
                'ApiKeyAuth': {
                    'type': 'apiKey',
                    'in': 'header',
                    'name': 'X-API-Key',
                    'description': 'Format: ak_live_<32 chars base62>',
                },
                'BearerAuth': {
                    'type': 'http',
                    'scheme': 'bearer',
                    'description': 'Alternatywa: Authorization: Bearer <key>',
                },
            },
            'schemas': {
                'ErrorResponse': {
                    'type': 'object',
                    'properties': {
                        'status': {'type': 'string', 'enum': ['error']},
                        'error': {'type': 'string'},
                        'code': {'type': 'string'},
                        'details': {'type': 'object'},
                    },
                    'required': ['status', 'error', 'code'],
                },
                'SuccessResponse': {
                    'type': 'object',
                    'properties': {
                        'status': {'type': 'string', 'enum': ['success']},
                        'data': {},
                        'meta': {'type': 'object'},
                    },
                    'required': ['status', 'data'],
                },
                'Product': {
                    'type': 'object',
                    'properties': {
                        'id': {'type': 'integer'},
                        'ean': {'type': 'string'},
                        'asin': {'type': 'string'},
                        'name': {'type': 'string'},
                        'description': {'type': 'string'},
                        'price': {
                            'type': 'object',
                            'properties': {
                                'net': {'type': 'number'},
                                'gross': {'type': 'number'},
                                'currency': {'type': 'string'},
                            },
                        },
                        'stock': {'type': 'integer'},
                        'category': {'type': 'string'},
                        'location': {'type': 'string'},
                        'status': {'type': 'string'},
                        'created_at': {'type': 'string', 'format': 'date-time'},
                    },
                },
                'Order': {
                    'type': 'object',
                    'properties': {
                        'id': {'type': 'integer'},
                        'external_order_id': {'type': 'string'},
                        'product_id': {'type': 'integer'},
                        'product_name': {'type': 'string'},
                        'quantity': {'type': 'integer'},
                        'price': {'type': 'number'},
                        'buyer': {'type': 'string'},
                        'address': {'type': 'string'},
                        'status': {'type': 'string',
                                   'enum': ['nowa', 'wyslana', 'zwrot', 'anulowana']},
                        'created_at': {'type': 'string', 'format': 'date-time'},
                    },
                },
                'Pallet': {
                    'type': 'object',
                    'properties': {
                        'id': {'type': 'integer'},
                        'name': {'type': 'string'},
                        'supplier': {'type': 'string'},
                        'purchase_price': {'type': 'number'},
                        'product_count': {'type': 'integer'},
                        'purchase_date': {'type': 'string', 'format': 'date'},
                        'notes': {'type': 'string'},
                    },
                },
                'Webhook': {
                    'type': 'object',
                    'properties': {
                        'id': {'type': 'integer'},
                        'url': {'type': 'string', 'format': 'uri'},
                        'events': {'type': 'array', 'items': {'type': 'string'}},
                        'active': {'type': 'boolean'},
                        'secret': {'type': 'string',
                                   'description': 'Shown ONCE on creation'},
                    },
                },
            },
        },
        'security': [{'ApiKeyAuth': []}, {'BearerAuth': []}],
        'paths': {
            '/health': {
                'get': {
                    'summary': 'Health check (public, no auth)',
                    'security': [],
                    'responses': {
                        '200': {
                            'description': 'Service alive',
                            'content': {'application/json': {'schema': {
                                '$ref': '#/components/schemas/SuccessResponse'}}},
                        },
                    },
                },
            },
            '/me': {
                'get': {
                    'summary': 'Current API key info',
                    'responses': {
                        '200': {'description': 'Key metadata'},
                        '401': {'description': 'Invalid/missing key'},
                    },
                },
            },
            '/products': {
                'get': {
                    'summary': 'List products',
                    'parameters': [
                        {'name': 'page', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1}},
                        {'name': 'per_page', 'in': 'query', 'schema': {'type': 'integer', 'maximum': 200}},
                        {'name': 'status', 'in': 'query', 'schema': {'type': 'string'}},
                        {'name': 'category', 'in': 'query', 'schema': {'type': 'string'}},
                        {'name': 'search', 'in': 'query', 'schema': {'type': 'string'}},
                    ],
                    'responses': {
                        '200': {'description': 'Paginated list'},
                        '401': {'description': 'Auth error'},
                    },
                },
                'post': {
                    'summary': 'Create product',
                    'requestBody': {
                        'required': True,
                        'content': {'application/json': {'schema': {
                            'type': 'object',
                            'required': ['name'],
                            'properties': {
                                'ean': {'type': 'string'},
                                'name': {'type': 'string'},
                                'description': {'type': 'string'},
                                'price_net': {'type': 'number'},
                                'price_gross': {'type': 'number'},
                                'stock': {'type': 'integer'},
                                'category': {'type': 'string'},
                                'location': {'type': 'string'},
                            },
                        }}},
                    },
                    'responses': {
                        '201': {'description': 'Created'},
                        '400': {'description': 'Validation error'},
                    },
                },
            },
            '/products/{id}': {
                'get': {
                    'summary': 'Get product by id',
                    'parameters': [
                        {'name': 'id', 'in': 'path', 'required': True,
                         'schema': {'type': 'integer'}},
                    ],
                    'responses': {
                        '200': {'description': 'Product'},
                        '404': {'description': 'Not found'},
                    },
                },
                'put': {
                    'summary': 'Update product (partial)',
                    'parameters': [
                        {'name': 'id', 'in': 'path', 'required': True,
                         'schema': {'type': 'integer'}},
                    ],
                    'requestBody': {
                        'required': True,
                        'content': {'application/json': {}},
                    },
                    'responses': {
                        '200': {'description': 'Updated'},
                        '404': {'description': 'Not found'},
                    },
                },
                'delete': {
                    'summary': 'Soft-delete product',
                    'parameters': [
                        {'name': 'id', 'in': 'path', 'required': True,
                         'schema': {'type': 'integer'}},
                    ],
                    'responses': {
                        '200': {'description': 'Deleted'},
                        '404': {'description': 'Not found'},
                    },
                },
            },
            '/products/{id}/stock': {
                'get': {
                    'summary': 'Get stock for a product',
                    'parameters': [
                        {'name': 'id', 'in': 'path', 'required': True,
                         'schema': {'type': 'integer'}},
                    ],
                    'responses': {'200': {'description': 'Stock'}},
                },
            },
            '/orders': {
                'get': {'summary': 'List orders'},
                'post': {
                    'summary': 'Create external order (triggers order.created webhook)',
                    'requestBody': {
                        'required': True,
                        'content': {'application/json': {'schema': {
                            'type': 'object',
                            'required': ['price'],
                            'properties': {
                                'product_id': {'type': 'integer'},
                                'product_name': {'type': 'string'},
                                'quantity': {'type': 'integer'},
                                'price': {'type': 'number'},
                                'buyer': {'type': 'string'},
                                'address': {'type': 'string'},
                                'external_order_id': {'type': 'string'},
                            },
                        }}},
                    },
                    'responses': {
                        '201': {'description': 'Created + webhook queued'},
                        '400': {'description': 'Validation'},
                    },
                },
            },
            '/orders/{id}': {
                'get': {'summary': 'Get order'},
                'delete': {'summary': 'Cancel order (soft)'},
            },
            '/orders/{id}/status': {
                'put': {
                    'summary': 'Update order status',
                    'requestBody': {
                        'required': True,
                        'content': {'application/json': {'schema': {
                            'type': 'object',
                            'required': ['status'],
                            'properties': {
                                'status': {'type': 'string',
                                           'enum': ['nowa', 'wyslana', 'zwrot', 'anulowana']},
                            },
                        }}},
                    },
                    'responses': {'200': {'description': 'Updated'}},
                },
            },
            '/stock': {
                'get': {'summary': 'Stock overview'},
            },
            '/stock/{id}': {
                'get': {'summary': 'Stock for product'},
            },
            '/stock/adjust': {
                'post': {'summary': 'Adjust stock (delta)'},
            },
            '/pallets': {
                'get': {'summary': 'List pallets'},
                'post': {'summary': 'Create pallet'},
            },
            '/pallets/{id}': {
                'get': {'summary': 'Get pallet'},
            },
            '/webhooks': {
                'get': {'summary': 'List webhooks'},
                'post': {
                    'summary': 'Register webhook (returns secret ONCE)',
                    'requestBody': {
                        'required': True,
                        'content': {'application/json': {'schema': {
                            'type': 'object',
                            'required': ['url', 'events'],
                            'properties': {
                                'url': {'type': 'string', 'format': 'uri'},
                                'events': {
                                    'type': 'array',
                                    'items': {'type': 'string',
                                              'enum': ['order.created', 'order.status_changed',
                                                      'sale.completed', 'return.received',
                                                      'product.stock_low', 'product.stock_zero']},
                                },
                            },
                        }}},
                    },
                    'responses': {'201': {'description': 'Created'}},
                },
            },
            '/webhooks/{id}': {
                'delete': {'summary': 'Delete webhook'},
            },
        },
    }


@api_v1_bp.route('/openapi.json', methods=['GET'])
def openapi_json():
    """OpenAPI 3.0 spec. Publiczne, bez auth."""
    return jsonify(build_spec())


_SWAGGER_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>AKCES HUB API v1 — Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css">
  <style>body{margin:0}.topbar{display:none}</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: '/api/v1/openapi.json',
      dom_id: '#swagger-ui',
      deepLinking: true,
      displayRequestDuration: true,
      tryItOutEnabled: true,
    });
  </script>
</body>
</html>'''


@api_v1_bp.route('/docs', methods=['GET'])
def swagger_ui():
    """Swagger UI interactive docs. Publiczne."""
    return _SWAGGER_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}
