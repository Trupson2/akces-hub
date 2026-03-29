#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Photo Daemon — panel statusowy Flask.
Wyświetla statystyki i listę jobów.

Uruchomienie:
    python status_app.py [--config config.yaml] [--port 5051]
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Dodaj katalog photo_daemon do ścieżki
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config, get_full_config
import db_utils

logger = logging.getLogger(__name__)

try:
    from flask import Flask, jsonify, render_template_string
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.error("[status_app] Flask nie jest zainstalowany! pip install Flask")

# ============================================================
# HTML TEMPLATES (inline, dark cyberpunk style)
# ============================================================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AKCES Photo Daemon — Status</title>
<style>
  :root {
    --bg: #0a0a0f;
    --bg-card: rgba(19,19,28,0.85);
    --neon-green: #beee00;
    --neon-cyan: #8ff5ff;
    --neon-pink: #ff6b9b;
    --text: #e8e8f0;
    --text-muted: #666680;
    --border: rgba(143,245,255,0.12);
    --green: #39d353;
    --red: #ff4757;
    --yellow: #ffa502;
    --blue: #70a1ff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Courier New', 'Roboto Mono', monospace;
    min-height: 100vh;
    padding: 20px;
  }
  .header {
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 24px;
  }
  .header h1 {
    font-size: 1.4rem;
    font-weight: 800;
    letter-spacing: 2px;
    text-transform: uppercase;
    background: linear-gradient(135deg, var(--neon-cyan), var(--neon-green));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .header .subtitle {
    font-size: 0.7rem;
    color: var(--text-muted);
    letter-spacing: 1px;
    margin-top: 4px;
  }
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 28px;
  }
  .stat-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-left: 3px solid var(--neon-cyan);
    padding: 16px;
  }
  .stat-card.new { border-left-color: var(--blue); }
  .stat-card.processing { border-left-color: var(--yellow); }
  .stat-card.done { border-left-color: var(--green); }
  .stat-card.error { border-left-color: var(--red); }
  .stat-card.total { border-left-color: var(--neon-cyan); }
  .stat-label {
    font-size: 0.6rem;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 8px;
  }
  .stat-value {
    font-size: 2rem;
    font-weight: 800;
    color: var(--text);
  }
  .stat-card.new .stat-value { color: var(--blue); }
  .stat-card.processing .stat-value { color: var(--yellow); }
  .stat-card.done .stat-value { color: var(--green); }
  .stat-card.error .stat-value { color: var(--red); }
  .section-title {
    font-size: 0.7rem;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
  }
  th {
    text-align: left;
    padding: 8px 12px;
    font-size: 0.62rem;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    color: var(--text);
    vertical-align: middle;
  }
  tr:hover td { background: rgba(143,245,255,0.03); }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    font-size: 0.6rem;
    letter-spacing: 1px;
    text-transform: uppercase;
    font-weight: 700;
    border-radius: 2px;
  }
  .badge-new { background: rgba(112,161,255,0.15); color: var(--blue); border: 1px solid rgba(112,161,255,0.3); }
  .badge-processing { background: rgba(255,165,2,0.15); color: var(--yellow); border: 1px solid rgba(255,165,2,0.3); }
  .badge-done { background: rgba(57,211,83,0.15); color: var(--green); border: 1px solid rgba(57,211,83,0.3); }
  .badge-error { background: rgba(255,71,87,0.15); color: var(--red); border: 1px solid rgba(255,71,87,0.3); }
  a { color: var(--neon-cyan); text-decoration: none; }
  a:hover { color: var(--neon-green); }
  .path { font-size: 0.72rem; color: var(--text-muted); max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .refresh-btn {
    display: inline-block;
    padding: 8px 16px;
    background: rgba(143,245,255,0.05);
    border: 1px solid var(--border);
    color: var(--neon-cyan);
    font-size: 0.72rem;
    letter-spacing: 1px;
    text-transform: uppercase;
    cursor: pointer;
    text-decoration: none;
    font-family: inherit;
    margin-bottom: 20px;
  }
  .refresh-btn:hover { background: rgba(143,245,255,0.1); color: var(--neon-green); }
  .empty { color: var(--text-muted); font-size: 0.85rem; padding: 20px 0; text-align: center; }
</style>
</head>
<body>
<div class="header">
  <h1>&#9; AKCES PHOTO DAEMON</h1>
  <div class="subtitle">STATUS PANEL &mdash; Photo Processing Queue</div>
</div>

<a href="/" class="refresh-btn">&#8635; Odśwież</a>

<div class="stats-grid">
  <div class="stat-card total">
    <div class="stat-label">Wszystkie</div>
    <div class="stat-value">{{ stats.total }}</div>
  </div>
  <div class="stat-card new">
    <div class="stat-label">Nowe</div>
    <div class="stat-value">{{ stats.new }}</div>
  </div>
  <div class="stat-card processing">
    <div class="stat-label">W trakcie</div>
    <div class="stat-value">{{ stats.processing }}</div>
  </div>
  <div class="stat-card done">
    <div class="stat-label">Gotowe</div>
    <div class="stat-value">{{ stats.done }}</div>
  </div>
  <div class="stat-card error">
    <div class="stat-label">Błędy</div>
    <div class="stat-value">{{ stats.error }}</div>
  </div>
</div>

<div class="section-title">Ostatnie zlecenia (max 50)</div>

{% if jobs %}
<table>
  <thead>
    <tr>
      <th>#</th>
      <th>SKU</th>
      <th>Produkt ID</th>
      <th>Status</th>
      <th>Plik</th>
      <th>Utworzono</th>
      <th>Aktualizacja</th>
    </tr>
  </thead>
  <tbody>
    {% for job in jobs %}
    <tr>
      <td><a href="/job/{{ job.id }}">#{{ job.id }}</a></td>
      <td>{{ job.sku or '—' }}</td>
      <td>{{ job.product_id or '—' }}</td>
      <td>
        <span class="badge badge-{{ job.status }}">{{ job.status }}</span>
      </td>
      <td><div class="path" title="{{ job.original_path }}">{{ job.original_path.replace('\\', '/').split('/')[-1] }}</div></td>
      <td style="font-size:0.75rem;color:var(--text-muted)">{{ job.created_at }}</td>
      <td style="font-size:0.75rem;color:var(--text-muted)">{{ job.updated_at }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<div class="empty">Brak zleceń — dodaj zdjęcia do katalogu INBOX/</div>
{% endif %}

</body>
</html>
"""

JOB_DETAIL_HTML = """<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job #{{ job.id }} — AKCES Photo Daemon</title>
<style>
  :root {
    --bg: #0a0a0f;
    --bg-card: rgba(19,19,28,0.85);
    --neon-green: #beee00;
    --neon-cyan: #8ff5ff;
    --neon-pink: #ff6b9b;
    --text: #e8e8f0;
    --text-muted: #666680;
    --border: rgba(143,245,255,0.12);
    --green: #39d353;
    --red: #ff4757;
    --yellow: #ffa502;
    --blue: #70a1ff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Courier New', 'Roboto Mono', monospace;
    min-height: 100vh;
    padding: 20px;
  }
  .header { border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }
  .header h1 { font-size: 1.2rem; font-weight: 800; letter-spacing: 2px; color: var(--neon-cyan); }
  .back-link { color: var(--neon-cyan); text-decoration: none; font-size: 0.78rem; display: inline-block; margin-bottom: 16px; }
  .back-link:hover { color: var(--neon-green); }
  .info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 28px; }
  .info-card { background: var(--bg-card); border: 1px solid var(--border); border-left: 3px solid var(--neon-cyan); padding: 14px; }
  .info-label { font-size: 0.6rem; letter-spacing: 1.5px; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px; }
  .info-value { font-size: 0.9rem; color: var(--text); word-break: break-all; }
  .section-title { font-size: 0.7rem; letter-spacing: 1.5px; text-transform: uppercase; color: var(--text-muted); margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }
  .badge { display: inline-block; padding: 2px 8px; font-size: 0.6rem; letter-spacing: 1px; text-transform: uppercase; font-weight: 700; border-radius: 2px; }
  .badge-new { background: rgba(112,161,255,0.15); color: var(--blue); border: 1px solid rgba(112,161,255,0.3); }
  .badge-processing { background: rgba(255,165,2,0.15); color: var(--yellow); border: 1px solid rgba(255,165,2,0.3); }
  .badge-done { background: rgba(57,211,83,0.15); color: var(--green); border: 1px solid rgba(57,211,83,0.3); }
  .badge-error { background: rgba(255,71,87,0.15); color: var(--red); border: 1px solid rgba(255,71,87,0.3); }
  .error-box { background: rgba(255,71,87,0.08); border: 1px solid rgba(255,71,87,0.3); border-left: 3px solid var(--red); padding: 14px; margin-bottom: 20px; color: var(--red); font-size: 0.82rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { text-align: left; padding: 8px 12px; font-size: 0.62rem; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted); border-bottom: 1px solid var(--border); }
  td { padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.04); color: var(--text); }
  tr:hover td { background: rgba(143,245,255,0.03); }
  .empty { color: var(--text-muted); font-size: 0.85rem; padding: 20px 0; text-align: center; }
</style>
</head>
<body>
<a href="/" class="back-link">&larr; Powrót do dashboardu</a>

<div class="header">
  <h1>Job #{{ job.id }}</h1>
</div>

{% if job.error_msg %}
<div class="error-box">
  <strong>BŁĄD:</strong><br>{{ job.error_msg }}
</div>
{% endif %}

<div class="info-grid">
  <div class="info-card">
    <div class="info-label">Status</div>
    <div class="info-value"><span class="badge badge-{{ job.status }}">{{ job.status }}</span></div>
  </div>
  <div class="info-card">
    <div class="info-label">SKU</div>
    <div class="info-value">{{ job.sku or '—' }}</div>
  </div>
  <div class="info-card">
    <div class="info-label">Produkt ID</div>
    <div class="info-value">{{ job.product_id or '—' }}</div>
  </div>
  <div class="info-card">
    <div class="info-label">Utworzono</div>
    <div class="info-value">{{ job.created_at }}</div>
  </div>
  <div class="info-card">
    <div class="info-label">Aktualizacja</div>
    <div class="info-value">{{ job.updated_at }}</div>
  </div>
  <div class="info-card" style="grid-column: 1 / -1;">
    <div class="info-label">Plik oryginalny</div>
    <div class="info-value">{{ job.original_path }}</div>
  </div>
  {% if job.work_path %}
  <div class="info-card" style="grid-column: 1 / -1;">
    <div class="info-label">Plik roboczy</div>
    <div class="info-value">{{ job.work_path }}</div>
  </div>
  {% endif %}
</div>

<div class="section-title">Przetworzone zdjęcia ({{ photos|length }} wariantów)</div>

{% if photos %}
<table>
  <thead>
    <tr>
      <th>#</th>
      <th>Wariant</th>
      <th>SKU</th>
      <th>Produkt ID</th>
      <th>Ścieżka</th>
      <th>Utworzono</th>
    </tr>
  </thead>
  <tbody>
    {% for photo in photos %}
    <tr>
      <td>{{ photo.id }}</td>
      <td><strong style="color:var(--neon-cyan)">{{ photo.variant }}</strong></td>
      <td>{{ photo.sku or '—' }}</td>
      <td>{{ photo.product_id or '—' }}</td>
      <td style="font-size:0.75rem;color:var(--text-muted)">{{ photo.path }}</td>
      <td style="font-size:0.75rem;color:var(--text-muted)">{{ photo.created_at }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<div class="empty">Brak przetworzonych zdjęć dla tego zlecenia</div>
{% endif %}

</body>
</html>
"""


def create_app(config_path: str | None = None) -> "Flask":
    """
    Tworzy aplikację Flask.

    Args:
        config_path: Ścieżka do config.yaml

    Returns:
        Aplikacja Flask
    """
    if not FLASK_AVAILABLE:
        raise ImportError("Flask nie jest zainstalowany! pip install Flask")

    cfg = load_config(config_path)
    db_path = cfg.get("db_path", "")

    if db_path:
        db_utils.init_tables(db_path)

    app = Flask(__name__)
    app.config["cfg"] = cfg

    @app.route("/")
    def dashboard():
        stats = db_utils.get_stats()
        jobs = db_utils.get_recent_jobs(limit=50)
        return render_template_string(DASHBOARD_HTML, stats=stats, jobs=jobs)

    @app.route("/job/<int:job_id>")
    def job_detail(job_id: int):
        job = db_utils.get_job(job_id)
        if not job:
            return f"<h1>Job #{job_id} nie istnieje</h1>", 404
        photos = db_utils.get_job_photos(job_id)
        return render_template_string(JOB_DETAIL_HTML, job=job, photos=photos)

    @app.route("/health")
    def health():
        stats = db_utils.get_stats()
        return jsonify({
            "status": "ok",
            "jobs_new": stats.get("new", 0),
            "jobs_processing": stats.get("processing", 0),
            "jobs_done": stats.get("done", 0),
            "jobs_error": stats.get("error", 0),
        })

    @app.route("/api/jobs")
    def api_jobs():
        """Endpoint dla integracji z Akces Hub panelem."""
        from flask import request as flask_request
        try:
            limit = min(int(flask_request.args.get("limit", 50)), 200)
        except (ValueError, TypeError):
            limit = 50
        jobs = db_utils.get_recent_jobs(limit=limit)
        stats = db_utils.get_stats()
        return jsonify({
            "jobs": jobs,
            "stats": stats,
        })

    return app


def main():
    parser = argparse.ArgumentParser(
        description="Photo Daemon Status App — panel HTTP"
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config.yaml"),
        help="Ścieżka do pliku config.yaml"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port HTTP (domyślnie z config.yaml: 5051)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)

    port = args.port or cfg.get("status_port", 5051)
    host = cfg.get("status_host", "0.0.0.0")

    app = create_app(args.config)

    print(f"[status_app] Startowanie na http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
