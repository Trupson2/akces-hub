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
