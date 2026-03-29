# -*- coding: utf-8 -*-
"""
Winning Products — Flask Blueprint.
Endpointy analizy produktów wartych sprzedaży.

URL prefix: /analityka
"""

import json
import logging
import threading
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

logger = logging.getLogger(__name__)

winning_bp = Blueprint("winning", __name__, url_prefix="/analityka")

# Stan analizy (thread-safe)
_analysis_lock = threading.Lock()
_analysis_running = False
_analysis_result: dict | None = None


@winning_bp.route("/winning", methods=["GET"])
def winning_page():
    """Renderuje stronę HTML z wynikami analizy."""
    try:
        return render_template("winning_products.html")
    except Exception as e:
        logger.error(f"[winning_bp] winning_page error: {e}")
        return f"<h1>Błąd renderowania strony</h1><pre>{e}</pre>", 500


@winning_bp.route("/winning/refresh", methods=["POST"])
def winning_refresh():
    """
    Uruchamia analizę winning products.
    Rate-limit: cooldown_minutes między kolejnymi skanami.

    Body (opcjonalne): {"categories": ["123", "456"]}

    Returns:
        JSON z wynikami lub błędem 429 przy cooldown
    """
    global _analysis_running, _analysis_result

    # Parsuj body
    categories = None
    try:
        body = request.get_json(silent=True) or {}
        categories = body.get("categories")
    except Exception:
        pass

    # Sprawdź czy Allegro jest zalogowane
    try:
        from modules.allegro_api import is_authenticated
        if not is_authenticated():
            return jsonify({
                "error": "❌ Allegro API nie jest połączone lub token wygasł. Idź do: Ustawienia → Allegro → Autoryzuj.",
                "allegro_auth": False
            }), 403
    except Exception:
        pass

    # Sprawdź cooldown (zwróci ValueError z JSON jeśli za wcześnie)
    try:
        from modules.winning_analyzer import run_winning_products_scan

        result = run_winning_products_scan(categories=categories)
        return jsonify(result), 200

    except ValueError as e:
        # Cooldown aktywny
        try:
            error_data = json.loads(str(e))
            return jsonify(error_data), 429
        except (json.JSONDecodeError, Exception):
            return jsonify({"error": str(e), "cooldown": True}), 429

    except Exception as e:
        logger.error(f"[winning_bp] winning_refresh error: {e}", exc_info=True)
        return jsonify({"error": f"Błąd analizy: {str(e)}"}), 500


@winning_bp.route("/winning/list", methods=["GET"])
def winning_list():
    """
    Zwraca listę produktów winning.

    Query params:
        limit: int (default 50, max 200)
        offset: int (default 0)
        min_score: float (default 0.0)

    Returns:
        JSON: {items, total, last_run}
    """
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = max(0, int(request.args.get("offset", 0)))
        min_score = float(request.args.get("min_score", 0.0))
    except (ValueError, TypeError):
        limit = 50
        offset = 0
        min_score = 0.0

    try:
        from modules.winning_analyzer import get_winning_products, get_winning_meta
        from modules.database import get_config

        items, total = get_winning_products(limit=limit, offset=offset, min_score=min_score)
        last_run = get_config("winning_last_run", "")

        return jsonify({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "last_run": last_run,
        })

    except Exception as e:
        logger.error(f"[winning_bp] winning_list error: {e}")
        return jsonify({"error": str(e), "items": [], "total": 0}), 500


@winning_bp.route("/winning/ignore/<int:item_id>", methods=["POST"])
def winning_ignore(item_id):
    """Toggle ignore dla produktu winning."""
    try:
        from modules.winning_analyzer import toggle_ignore
        new_state = toggle_ignore(item_id)
        return jsonify({"success": True, "ignored": new_state})
    except Exception as e:
        logger.error(f"[winning_bp] winning_ignore error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@winning_bp.route("/winning/zakupy/<int:item_id>", methods=["POST"])
def winning_zakupy(item_id):
    """Dodaje produkt do listy zakupów."""
    try:
        from modules.winning_analyzer import add_to_zakupy_lista
        result = add_to_zakupy_lista(item_id)
        return jsonify(result), 200 if result.get("success") else 409
    except Exception as e:
        logger.error(f"[winning_bp] winning_zakupy error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@winning_bp.route("/winning/zakupy-lista", methods=["GET"])
def winning_zakupy_lista():
    """Zwraca listę zakupów jako JSON."""
    try:
        from modules.winning_analyzer import get_zakupy_lista
        status_filter = request.args.get("status")
        items = get_zakupy_lista(status_filter=status_filter)
        return jsonify({"items": items, "total": len(items)})
    except Exception as e:
        logger.error(f"[winning_bp] winning_zakupy_lista error: {e}")
        return jsonify({"error": str(e), "items": []}), 500


@winning_bp.route("/winning/zakupy-status/<int:item_id>", methods=["POST"])
def winning_zakupy_update(item_id):
    """Zmienia status pozycji na liście zakupów."""
    try:
        from modules.winning_analyzer import update_zakupy_status
        body = request.get_json(silent=True) or {}
        status = body.get("status", "ordered")
        allowed = {"new", "ordered", "received", "skipped"}
        if status not in allowed:
            return jsonify({"success": False, "error": "Nieprawidłowy status"}), 400
        ok = update_zakupy_status(item_id, status)
        return jsonify({"success": ok})
    except Exception as e:
        logger.error(f"[winning_bp] winning_zakupy_update error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@winning_bp.route("/winning/test-allegro", methods=["GET"])
def winning_test_allegro():
    """
    Diagnostyka — sprawdza połączenie z Allegro i pobiera sample oferty.
    Otwórz /analityka/winning/test-allegro w przeglądarce.
    """
    out = []
    try:
        from modules.allegro_api import allegro_request, is_authenticated
        out.append(f"is_authenticated: {is_authenticated()}")

        # Test 1: kategorie domyślne
        test_cats = ["258682", "257993", "4029"]
        for cat in test_cats:
            result = allegro_request("GET", "/offers/listing", params={
                "category.id": cat,
                "limit": 5,
                "sort": "-watchersCount",
            })
            if isinstance(result, tuple):
                data, err = result
            else:
                data, err = result, None

            if err:
                out.append(f"cat {cat}: ERROR → {err}")
            elif data is None:
                out.append(f"cat {cat}: data=None")
            else:
                items = data.get("items", {}) or {}
                regular = items.get("regular", []) or []
                promoted = items.get("promoted", []) or []
                keys = list(data.keys())
                out.append(f"cat {cat}: keys={keys}, regular={len(regular)}, promoted={len(promoted)}")
                if regular:
                    out.append(f"  sample: {regular[0].get('name','')[:80]}")

        # Test 2: prosty search bez kategorii
        result2 = allegro_request("GET", "/offers/listing", params={"phrase": "telefon", "limit": 3})
        if isinstance(result2, tuple):
            data2, err2 = result2
        else:
            data2, err2 = result2, None
        if err2:
            out.append(f"phrase test: ERROR → {err2}")
        else:
            items2 = (data2 or {}).get("items", {}) or {}
            out.append(f"phrase 'telefon': regular={len(items2.get('regular',[]))}, promoted={len(items2.get('promoted',[]))}")

    except Exception as e:
        import traceback
        out.append(f"EXCEPTION: {e}")
        out.append(traceback.format_exc())

    return "<pre>" + "\n".join(out) + "</pre>"


@winning_bp.route("/winning/meta", methods=["GET"])
def winning_meta():
    """
    Zwraca metadane ostatnich runów analizy.

    Returns:
        JSON: lista 10 ostatnich runów
    """
    try:
        from modules.winning_analyzer import get_winning_meta
        meta = get_winning_meta(limit=10)
        return jsonify({"runs": meta})
    except Exception as e:
        logger.error(f"[winning_bp] winning_meta error: {e}")
        return jsonify({"error": str(e), "runs": []}), 500
