"""Lightweight request validation / serialization.

Nie uzywamy marshmallow ani pydantic — zeby nie dodawac 5MB zaleznosci
na Raspberry Pi. Robimy rzeczna walidacje: dict field matching + typ coercion
+ znane bledy zamieniane na VALIDATION_ERROR z details {field: reason}.

Uzycie:
    schema = ProductCreateSchema()
    data, errors = schema.validate(request.json)
    if errors:
        return error_response('Validation failed', 'VALIDATION_ERROR', 400,
                              details=errors)
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple


class ValidationError(Exception):
    def __init__(self, errors: Dict[str, str]):
        self.errors = errors
        super().__init__(f'Validation failed: {errors}')


class Field:
    """Opis jednego pola w schemacie.

    typ: str|int|float|bool|list|dict
    required: czy musi byc obecne
    default: uzyty gdy brak a not required
    choices: jesli podane — wartosc musi byc jedna z nich
    min_length / max_length: dla str
    min_value / max_value: dla liczb
    """

    def __init__(self, typ, required=False, default=None, choices=None,
                 min_length=None, max_length=None, min_value=None, max_value=None):
        self.typ = typ
        self.required = required
        self.default = default
        self.choices = choices
        self.min_length = min_length
        self.max_length = max_length
        self.min_value = min_value
        self.max_value = max_value

    def validate(self, value):
        """Walidacja pojedynczego pola. Rzuca ValueError z komunikatem."""
        if value is None:
            return None
        t = self.typ
        # Coercion: jesli przyszedl inny typ, sprobuj zrzutowac
        if t is int:
            try:
                value = int(value)
            except (ValueError, TypeError):
                raise ValueError('must be integer')
        elif t is float:
            try:
                value = float(value)
            except (ValueError, TypeError):
                raise ValueError('must be number')
        elif t is bool:
            if isinstance(value, bool):
                pass
            elif isinstance(value, str):
                value = value.lower() in ('true', '1', 'yes', 'on')
            elif isinstance(value, int):
                value = bool(value)
            else:
                raise ValueError('must be boolean')
        elif t is str:
            if not isinstance(value, str):
                value = str(value)
        elif t is list:
            if not isinstance(value, list):
                raise ValueError('must be list')
        elif t is dict:
            if not isinstance(value, dict):
                raise ValueError('must be object')

        # Constraints
        if self.choices is not None and value not in self.choices:
            raise ValueError(f'must be one of {list(self.choices)}')
        if self.min_length is not None and hasattr(value, '__len__') and len(value) < self.min_length:
            raise ValueError(f'must be at least {self.min_length} long')
        if self.max_length is not None and hasattr(value, '__len__') and len(value) > self.max_length:
            raise ValueError(f'must be at most {self.max_length} long')
        if self.min_value is not None and isinstance(value, (int, float)) and value < self.min_value:
            raise ValueError(f'must be >= {self.min_value}')
        if self.max_value is not None and isinstance(value, (int, float)) and value > self.max_value:
            raise ValueError(f'must be <= {self.max_value}')

        return value


class Schema:
    """Minimalna klasa bazowa. Subklasy definiuja `fields: dict[name, Field]`."""

    fields: Dict[str, Field] = {}

    def validate(self, payload: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Returns (clean_data, errors).

        Jesli `errors` niepuste — caller powinien zwrocic 400.
        """
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return {}, {'_body': 'must be a JSON object'}

        clean: Dict[str, Any] = {}
        errors: Dict[str, str] = {}

        for name, field in self.fields.items():
            if name not in payload:
                if field.required:
                    errors[name] = 'field is required'
                    continue
                if field.default is not None:
                    clean[name] = field.default
                continue
            try:
                clean[name] = field.validate(payload[name])
            except ValueError as e:
                errors[name] = str(e)

        return clean, errors


# ---------------------------------------------------------------------------
# Konkretne schematy
# ---------------------------------------------------------------------------

class ProductCreateSchema(Schema):
    fields = {
        'ean': Field(str, required=False, max_length=32),
        'name': Field(str, required=True, min_length=1, max_length=500),
        'description': Field(str, required=False, max_length=20000),
        'price_net': Field(float, required=False, min_value=0),
        'price_gross': Field(float, required=False, min_value=0),
        'stock': Field(int, required=False, min_value=0, default=0),
        'category': Field(str, required=False, max_length=100),
        'location': Field(str, required=False, max_length=100),
        'asin': Field(str, required=False, max_length=32),
        'status': Field(str, required=False, choices=['magazyn', 'sprzedany', 'zwrot', 'deleted']),
    }


class ProductUpdateSchema(Schema):
    """Wszystkie pola opcjonalne — PATCH-like PUT."""
    fields = {
        'ean': Field(str, required=False, max_length=32),
        'name': Field(str, required=False, min_length=1, max_length=500),
        'description': Field(str, required=False, max_length=20000),
        'price_net': Field(float, required=False, min_value=0),
        'price_gross': Field(float, required=False, min_value=0),
        'stock': Field(int, required=False, min_value=0),
        'category': Field(str, required=False, max_length=100),
        'location': Field(str, required=False, max_length=100),
        'status': Field(str, required=False, choices=['magazyn', 'sprzedany', 'zwrot', 'deleted']),
    }


class OrderCreateSchema(Schema):
    fields = {
        'product_id': Field(int, required=False, min_value=1),
        'product_name': Field(str, required=False, max_length=500),
        'quantity': Field(int, required=False, default=1, min_value=1),
        'price': Field(float, required=True, min_value=0),
        'buyer': Field(str, required=False, max_length=200),
        'address': Field(str, required=False, max_length=1000),
        'platform': Field(str, required=False, max_length=50),
        'external_order_id': Field(str, required=False, max_length=100),
        'status': Field(str, required=False, default='nowa',
                        choices=['nowa', 'wyslana', 'zwrot', 'anulowana']),
    }


class OrderStatusSchema(Schema):
    fields = {
        'status': Field(str, required=True,
                        choices=['nowa', 'wyslana', 'zwrot', 'anulowana']),
    }


class StockAdjustSchema(Schema):
    fields = {
        'product_id': Field(int, required=True, min_value=1),
        'delta': Field(int, required=True),
        'reason': Field(str, required=False, max_length=200),
    }


class PalletCreateSchema(Schema):
    fields = {
        'name': Field(str, required=True, min_length=1, max_length=200),
        'supplier': Field(str, required=False, max_length=200),
        'purchase_price': Field(float, required=False, default=0, min_value=0),
        'product_count': Field(int, required=False, default=0, min_value=0),
        'notes': Field(str, required=False, max_length=2000),
        'location': Field(str, required=False, max_length=100),
    }


class WebhookCreateSchema(Schema):
    fields = {
        'url': Field(str, required=True, min_length=10, max_length=1000),
        'events': Field(list, required=True, min_length=1),
    }


class ApiKeyCreateSchema(Schema):
    fields = {
        'name': Field(str, required=True, min_length=1, max_length=200),
        'rate_limit_per_min': Field(int, required=False, default=60,
                                    min_value=1, max_value=100000),
    }
