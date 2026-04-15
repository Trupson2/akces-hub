"""Admin UI do zarzadzania API keys.

Proste HTML + JSON endpointy pod /api/admin/keys — wymagaja require_admin.
Plain key jest pokazywany RAZ przy generacji (user musi skopiowac).

Endpointy:
  GET  /api/admin/keys             -> HTML list (admin UI)
  GET  /api/admin/keys/list        -> JSON list kluczy
  POST /api/admin/keys/create      -> tworzy klucz, zwraca PLAIN w response (raz)
  POST /api/admin/keys/<id>/revoke -> revoke key
  GET  /api/admin/keys/stats/<id>  -> usage stats (last 24h)
"""
from __future__ import annotations

from flask import jsonify, request

from .schemas import ApiKeyCreateSchema


def register_admin_routes(app):
    """Rejestruje route'y w globalnym app (nie w blueprint) zeby /api/admin
    bylo spojne z innymi admin endpointami w aplikacji.
    """
    from modules.auth import require_admin

    @app.route('/api/admin/keys', methods=['GET'])
    @require_admin
    def api_admin_keys_ui():
        """HTML panelu zarzadzania API keys."""
        return _ADMIN_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

    @app.route('/api/admin/keys/list', methods=['GET'])
    @require_admin
    def api_admin_keys_list():
        from modules.database import get_db
        conn = get_db()
        rows = conn.execute(
            'SELECT id, key_prefix, name, created_at, last_used_at, revoked_at, '
            'rate_limit_per_min FROM api_keys ORDER BY created_at DESC'
        ).fetchall()
        return jsonify({
            'keys': [
                {
                    'id': r['id'],
                    'prefix': r['key_prefix'],
                    'name': r['name'],
                    'created_at': r['created_at'],
                    'last_used_at': r['last_used_at'],
                    'revoked_at': r['revoked_at'],
                    'rate_limit_per_min': r['rate_limit_per_min'],
                    'active': r['revoked_at'] is None,
                }
                for r in rows
            ]
        })

    @app.route('/api/admin/keys/create', methods=['POST'])
    @require_admin
    def api_admin_keys_create():
        if not request.is_json:
            return jsonify({'error': 'JSON required'}), 400
        payload = request.get_json(silent=True) or {}
        data, errors = ApiKeyCreateSchema().validate(payload)
        if errors:
            return jsonify({'error': 'Validation failed', 'details': errors}), 400

        from .auth import generate_api_key
        plain, key_hash, key_prefix = generate_api_key()

        from modules.database import get_db
        conn = get_db()
        cur = conn.execute(
            'INSERT INTO api_keys (key_hash, key_prefix, name, rate_limit_per_min) '
            'VALUES (?, ?, ?, ?)',
            (key_hash, key_prefix, data['name'],
             int(data.get('rate_limit_per_min') or 60)),
        )
        conn.commit()

        # Audit log
        try:
            from modules.database import log_admin_action
            log_admin_action('api_key_created', {
                'id': cur.lastrowid, 'name': data['name'], 'prefix': key_prefix})
        except Exception:
            pass

        return jsonify({
            'id': cur.lastrowid,
            'name': data['name'],
            'prefix': key_prefix,
            'key': plain,  # pokazywany RAZ
            'rate_limit_per_min': int(data.get('rate_limit_per_min') or 60),
            'warning': 'Save this key NOW. You will not see it again.',
        }), 201

    @app.route('/api/admin/keys/<int:key_id>/revoke', methods=['POST'])
    @require_admin
    def api_admin_keys_revoke(key_id):
        from modules.database import get_db
        conn = get_db()
        row = conn.execute(
            'SELECT id, name, revoked_at FROM api_keys WHERE id = ?', (key_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Key not found'}), 404
        if row['revoked_at']:
            return jsonify({'error': 'Already revoked', 'revoked_at': row['revoked_at']}), 409
        conn.execute(
            'UPDATE api_keys SET revoked_at = CURRENT_TIMESTAMP WHERE id = ?',
            (key_id,),
        )
        conn.commit()

        try:
            from modules.database import log_admin_action
            log_admin_action('api_key_revoked', {'id': key_id, 'name': row['name']})
        except Exception:
            pass

        return jsonify({'id': key_id, 'revoked': True})

    @app.route('/api/admin/keys/stats/<int:key_id>', methods=['GET'])
    @require_admin
    def api_admin_keys_stats(key_id):
        from modules.database import get_db
        conn = get_db()
        total = conn.execute(
            "SELECT COUNT(*) as c FROM api_usage_log "
            "WHERE api_key_id = ? AND created_at >= datetime('now', '-1 day')",
            (key_id,),
        ).fetchone()['c']
        by_endpoint = conn.execute(
            "SELECT endpoint, COUNT(*) as c FROM api_usage_log "
            "WHERE api_key_id = ? AND created_at >= datetime('now', '-1 day') "
            "GROUP BY endpoint ORDER BY c DESC LIMIT 20",
            (key_id,),
        ).fetchall()
        by_status = conn.execute(
            "SELECT status_code, COUNT(*) as c FROM api_usage_log "
            "WHERE api_key_id = ? AND created_at >= datetime('now', '-1 day') "
            "GROUP BY status_code",
            (key_id,),
        ).fetchall()
        return jsonify({
            'key_id': key_id,
            'last_24h': {
                'total_requests': total,
                'by_endpoint': [{'endpoint': r['endpoint'], 'count': r['c']}
                                for r in by_endpoint],
                'by_status': [{'status_code': r['status_code'], 'count': r['c']}
                              for r in by_status],
            },
        })


_ADMIN_HTML = '''<!DOCTYPE html>
<html lang="pl"><head>
<meta charset="UTF-8">
<title>API Keys - Admin</title>
<style>
body{font-family:system-ui,sans-serif;background:#0e0e10;color:#f9f5f8;padding:24px;max-width:1000px;margin:auto}
h1{color:#8ff5ff}
button{background:#8ff5ff;color:#0e0e10;padding:8px 16px;border:0;border-radius:6px;cursor:pointer;font-weight:600}
button.danger{background:#ff6b9b;color:white}
input{background:#19191c;color:#f9f5f8;padding:8px;border:1px solid #333;border-radius:4px;margin:4px}
table{width:100%;border-collapse:collapse;margin-top:16px}
th,td{padding:8px;border-bottom:1px solid #333;text-align:left}
th{color:#8ff5ff}
.card{background:#19191c;padding:16px;border-radius:8px;margin:12px 0}
.plain-key{background:#0e0e10;padding:12px;border:2px solid #beee00;border-radius:4px;font-family:monospace;word-break:break-all;color:#beee00}
.muted{color:#adaaad;font-size:0.9em}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.85em}
.tag.active{background:#beee00;color:#0e0e10}
.tag.revoked{background:#ff6b9b;color:white}
</style></head><body>
<h1>API Keys</h1>
<p class="muted">Zarzadzanie kluczami do publicznego REST API v1 (<code>/api/v1/*</code>).
Dokumentacja: <a href="/api/v1/docs" style="color:#8ff5ff">Swagger UI</a></p>

<div class="card">
  <h3>Generuj nowy klucz</h3>
  <input id="k-name" placeholder="Nazwa (np. Sklep firmy X)" style="width:300px">
  <input id="k-limit" placeholder="Rate limit/min" value="60" style="width:120px" type="number">
  <button onclick="createKey()">Generuj</button>
  <div id="new-key-box" style="display:none;margin-top:16px">
    <div><strong>Nowy klucz (SKOPIUJ TERAZ — wiecej nie zobaczysz!):</strong></div>
    <div class="plain-key" id="new-key-val"></div>
    <button onclick="document.getElementById('new-key-box').style.display='none'">Zamknij</button>
  </div>
</div>

<div class="card">
  <h3>Lista kluczy</h3>
  <table id="keys-table">
    <thead><tr>
      <th>ID</th><th>Prefix</th><th>Nazwa</th><th>Limit/min</th>
      <th>Utworzony</th><th>Ostatnio uzyty</th><th>Status</th><th></th>
    </tr></thead>
    <tbody id="keys-body"><tr><td colspan="8" class="muted">Laduje...</td></tr></tbody>
  </table>
</div>

<script>
async function loadKeys(){
  const r = await fetch('/api/admin/keys/list');
  const j = await r.json();
  const body = document.getElementById('keys-body');
  if(!j.keys || !j.keys.length){
    body.innerHTML = '<tr><td colspan="8" class="muted">Brak kluczy. Wygeneruj pierwszy.</td></tr>';
    return;
  }
  body.innerHTML = j.keys.map(k => `
    <tr>
      <td>${k.id}</td>
      <td><code>${k.prefix}...</code></td>
      <td>${escapeHtml(k.name)}</td>
      <td>${k.rate_limit_per_min}</td>
      <td class="muted">${k.created_at || ''}</td>
      <td class="muted">${k.last_used_at || 'nigdy'}</td>
      <td>${k.active ? '<span class="tag active">aktywny</span>' : '<span class="tag revoked">revoked</span>'}</td>
      <td>${k.active ? `<button class="danger" onclick="revokeKey(${k.id})">Revoke</button>` : ''}</td>
    </tr>
  `).join('');
}

async function createKey(){
  const name = document.getElementById('k-name').value.trim();
  const limit = parseInt(document.getElementById('k-limit').value || '60', 10);
  if(!name){alert('Podaj nazwe');return;}
  const r = await fetch('/api/admin/keys/create', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, rate_limit_per_min: limit}),
  });
  const j = await r.json();
  if(!r.ok){alert('Blad: ' + (j.error || r.status));return;}
  document.getElementById('new-key-val').textContent = j.key;
  document.getElementById('new-key-box').style.display = 'block';
  document.getElementById('k-name').value = '';
  await loadKeys();
}

async function revokeKey(id){
  if(!confirm('Revoke klucz #' + id + '?')) return;
  const r = await fetch(`/api/admin/keys/${id}/revoke`, {method: 'POST'});
  if(!r.ok){alert('Blad');return;}
  await loadKeys();
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[c]));
}

loadKeys();
</script>
</body></html>'''
