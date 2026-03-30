# -*- coding: utf-8 -*-
"""
Winning Products — Flask Blueprint.
Teraz oparty na Winning Scout (skaner nowych produktów).

URL prefix: /analityka
"""

import json
import logging
import threading

from flask import Blueprint, jsonify, render_template, request

logger = logging.getLogger(__name__)

winning_bp = Blueprint("winning", __name__, url_prefix="/analityka")

# Stan analizy (thread-safe)
_analysis_lock = threading.Lock()


@winning_bp.route("/winning", methods=["GET"])
def winning_page():
    """Renderuje stronę HTML Winning Scout."""
    try:
        return render_template("winning_products.html")
    except Exception as e:
        logger.error(f"[winning_bp] winning_page error: {e}")
        return f"<h1>Błąd renderowania strony</h1><pre>{e}</pre>", 500


@winning_bp.route("/winning/refresh", methods=["POST"])
def winning_refresh():
    """Uruchamia skan Winning Scout."""
    try:
        from modules.winning_scout import run_scout_scan
        result = run_scout_scan()

        if result.get('error'):
            status_code = 429 if result.get('cooldown') else 500
            return jsonify(result), status_code

        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[winning_bp] winning_refresh error: {e}", exc_info=True)
        return jsonify({"error": f"Błąd skanu: {str(e)}"}), 500


@winning_bp.route("/winning/list", methods=["GET"])
def winning_list():
    """Zwraca listę kandydatów z ostatniego skanu."""
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = max(0, int(request.args.get("offset", 0)))
        min_score = float(request.args.get("min_score", 0.0))
        status_filter = request.args.get("status", "")
        batch_id = request.args.get("batch_id", "")
    except (ValueError, TypeError):
        limit, offset, min_score = 50, 0, 0.0
        status_filter, batch_id = "", ""

    try:
        from modules.winning_scout import get_scout_results, get_scout_stats

        # batch_id pusty = pokaż WSZYSTKIE skany (nie kasujemy starych)
        items, total = get_scout_results(
            batch_id=batch_id or None,
            status_filter=status_filter or None,
            limit=limit,
            offset=offset,
            min_score=min_score,
        )

        return jsonify({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "batch_id": batch_id,
        })

    except Exception as e:
        logger.error(f"[winning_bp] winning_list error: {e}")
        return jsonify({"error": str(e), "items": [], "total": 0}), 500


@winning_bp.route("/winning/stats", methods=["GET"])
def winning_stats():
    """Zwraca statystyki ostatniego skanu."""
    try:
        from modules.winning_scout import get_scout_stats
        return jsonify(get_scout_stats())
    except Exception as e:
        logger.error(f"[winning_bp] winning_stats error: {e}")
        return jsonify({"error": str(e)}), 500


@winning_bp.route("/winning/unlock", methods=["POST"])
def winning_unlock():
    """Wymusza reset locka skanu (po crashu)."""
    try:
        from modules.winning_scout import force_unlock
        force_unlock()
        return jsonify({"success": True, "message": "Lock zresetowany"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@winning_bp.route("/winning/meta", methods=["GET"])
def winning_meta():
    """Kompatybilność wsteczna — zwraca puste dane."""
    return jsonify({"runs": []})
