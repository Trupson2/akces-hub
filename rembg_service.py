# -*- coding: utf-8 -*-
"""
Rembg VPS Microservice — usuwanie tla ze zdjec produktowych
Uruchamiany na VPS, obsluguje requesty z Pi/apki przez HTTP.

Instalacja na VPS:
    pip install flask rembg[cpu] pillow gunicorn

Uruchomienie:
    export REMBG_API_KEY=twoj-tajny-klucz
    gunicorn -w 2 -b 0.0.0.0:5050 rembg_service:app

Lub z systemd (produkcja):
    [Unit]
    Description=Rembg Background Removal Service
    After=network.target

    [Service]
    User=www-data
    WorkingDirectory=/opt/rembg
    Environment=REMBG_API_KEY=twoj-tajny-klucz
    ExecStart=/usr/local/bin/gunicorn -w 2 -b 0.0.0.0:5050 rembg_service:app
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
"""

import os
import time
from flask import Flask, request, jsonify, Response
from PIL import Image
from io import BytesIO

app = Flask(__name__)

# API key z env var (opcjonalny)
API_KEY = os.environ.get('REMBG_API_KEY', '')

# Lazy load rembg (pierwszy import sciaga model ~170MB)
_rembg_remove = None


def get_rembg():
    global _rembg_remove
    if _rembg_remove is None:
        from rembg import remove
        _rembg_remove = remove
        print("[rembg_service] Model zaladowany!")
    return _rembg_remove


def check_api_key():
    """Sprawdza API key jesli ustawiony"""
    if not API_KEY:
        return True
    key = request.headers.get('X-API-Key', '')
    return key == API_KEY


@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    try:
        get_rembg()
        rembg_ok = True
    except Exception:
        rembg_ok = False

    return jsonify({
        'status': 'ok',
        'rembg': rembg_ok,
        'auth_required': bool(API_KEY),
    })


@app.route('/remove-bg', methods=['POST'])
def remove_bg():
    """
    Usuwa tlo ze zdjecia.
    POST multipart/form-data z polem 'image'.
    Zwraca JPEG z bialym tlem.
    """
    if not check_api_key():
        return jsonify({'error': 'Unauthorized'}), 401

    if 'image' not in request.files:
        return jsonify({'error': 'Brak pola image'}), 400

    file = request.files['image']
    if not file:
        return jsonify({'error': 'Pusty plik'}), 400

    # Max 10MB
    image_bytes = file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        return jsonify({'error': 'Plik za duzy (max 10MB)'}), 413

    max_dim = request.args.get('max_dim', 1024, type=int)
    max_dim = min(max_dim, 2048)  # Limit bezpieczenstwa

    try:
        start = time.time()

        # Otworz i zmniejsz
        img = Image.open(BytesIO(image_bytes)).convert('RGBA')
        if max(img.width, img.height) > max_dim:
            ratio = max_dim / max(img.width, img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

        # Konwertuj do PNG dla rembg
        img_buf = BytesIO()
        img.save(img_buf, format='PNG')
        img_buf.seek(0)

        # Usun tlo
        rembg = get_rembg()
        result_bytes = rembg(img_buf.read())
        result_img = Image.open(BytesIO(result_bytes)).convert('RGBA')

        # Biale tlo
        white_bg = Image.new('RGBA', result_img.size, (255, 255, 255, 255))
        white_bg.paste(result_img, mask=result_img.split()[3])
        final = white_bg.convert('RGB')

        # Zapisz jako JPEG
        output = BytesIO()
        final.save(output, format='JPEG', quality=92)
        output.seek(0)

        elapsed = time.time() - start
        print(f"[rembg_service] OK {img.width}x{img.height} -> {final.width}x{final.height} ({elapsed:.1f}s)")

        return Response(
            output.read(),
            mimetype='image/jpeg',
            headers={
                'X-Processing-Time': f'{elapsed:.1f}s',
                'X-Original-Size': f'{img.width}x{img.height}',
            }
        )

    except Exception as e:
        print(f"[rembg_service] Error: {e}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("[rembg_service] Starting... (pierwszy request sciagnie model ~170MB)")
    app.run(host='0.0.0.0', port=5050, debug=False)
