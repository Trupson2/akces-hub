# Sklepakces.pl ↔ Akces Hub Integration (Faza 3)

> **Status:** ✅ Implementation complete. 33 pytest PASS, PHP↔Python HMAC parity verified.
> **Branch:** `feature/sklepakces-integration` → merge to main after live RPi5 deploy test.
> **Plugin counterpart:** [Trupson2/sklepakces](https://github.com/Trupson2) — plugin
> `akces-hub-connector` v1.1.0 (Faza 2 zamknięta, 56/56 PHPUnit PASS).

---

## Co to jest

Pythonowa strona integracji WordPress plugin (sklepakces.pl)↔Akces Hub (RPi5).
Plugin po `woocommerce_payment_complete` wysyła HMAC-signed POST do Hub'a.
Hub validuje, persystuje do `sklepakces_*` tabel, alertuje Adriana przez Telegram.

**Faza 4 (przyszłość):** triggerowanie integracji z Paletomat (item allocation),
Magazynier (cross-channel inventory), Allegro (auto-listing).

---

## Architektura — endpoints

Pod `/api/v1/sklepakces/*` prefix (osobny namespace od `api_v1`):

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/sklepakces/health` | none | Connection diagnostic (Redis status + version) |
| POST | `/api/v1/sklepakces/orders` | HMAC + nonce | Order webhook po `woocommerce_payment_complete` |
| POST | `/api/v1/sklepakces/products` | HMAC + nonce | Product create/update notification |
| POST | `/api/v1/sklepakces/stock_sync` | HMAC + nonce | Inventory change notification |
| GET | `/api/v1/sklepakces/inventory/<sku>` | HMAC + nonce | Stock query (Hub → response) |

**3-warstwowa walidacja** każdego HMAC-protected endpointu:
1. **Timestamp window 300s** (anti-replay) — `abs(now - ts) > 300` → 401
2. **Constant-time signature compare** (`hmac.compare_digest`) — 401 jeśli mismatch
3. **Nonce uniqueness** (Redis 24h TTL, fallback in-memory) — 403 jeśli replay

---

## Why two webhook systems? (`api_v1` vs `sklepakces`)

Akces Hub ma **dwa różne webhook systemy** służące różnym celom:

### `modules/api_v1/` — OUTBOUND webhook delivery

- **Direction:** Hub → external integrators
- **Auth:** `X-API-Key: ak_live_*` (bcrypt hash w `api_keys`)
- **Use case:** future partnerzy (sklepy zewnętrzne, custom integracje, Allegro/Amazon)
- **Tabele:** `api_keys`, `api_usage_log`, `webhooks`, `webhook_deliveries`
- **Events:** `order.created`, `sale.completed`, `return.received`, `product.stock_low`, etc.
- **Worker:** background daemon thread `start_delivery_worker()`, retry exp backoff 5×

### `modules/sklepakces_*.py` — INBOUND webhook receiver (Faza 3, NEW)

- **Direction:** Plugin WooCommerce sklepakces.pl → Hub
- **Auth:** HMAC-SHA256 (shared secret z plugin'owym `akces_hub_hmac_secret`)
- **Use case:** **dedicated** integration sklepakces.pl (one specific shop)
- **Tabele:** `sklepakces_orders`, `sklepakces_products`, `sklepakces_stock_log`, `sklepakces_webhook_log`
- **Worker:** brak (synchronous handlers — plugin czeka na 200 OK)

**Future option:** Migrate sklepakces do api_v1 jeśli/gdy api_v1 proves viable for
multi-tenant integration. Albo deprecate api_v1 jeśli sklepakces zostaje primary
case. Na razie — czysta separacja, 0 ryzyka regresji w istniejącym api_v1.

---

## Setup

### 1. Install dependencies

```bash
cd ~/akces-hub
pip install -r requirements.txt
# Plus redis-server jeśli brak:
sudo apt install redis-server
sudo systemctl enable --now redis-server
redis-cli ping  # → PONG
```

### 2. Configure HMAC secret

**Krytyczne:** `sklepakces_hmac_secret` w Hub MUSI być IDENTYCZNY z WP option
`akces_hub_hmac_secret` w plugin'ie (wpisany przez admin w WC → Akces Hub → tab Hub).

Generuj nowy secret + ustaw w obu miejscach:

```bash
# 1. Generuj na RPi5:
python -c "import secrets; print(secrets.token_hex(32))"
# Output: <64-hex-chars>

# 2. Zapisz w Hub config:
python -c "from modules.database import set_config; set_config('sklepakces_hmac_secret', '<paste-secret>')"

# 3. Zapisz w plugin WP option (LocalWP lub lh.pl wp-cli):
wp option update akces_hub_hmac_secret '<paste-same-secret>'
```

### 3. Configure Hub URL w plugin

Plugin musi wiedzieć gdzie wysyłać webhooki:

```bash
# Lokalnie (LocalWP):
wp option update akces_hub_url 'http://192.168.100.200:5000'

# Production (Cloudflare Tunnel):
wp option update akces_hub_url 'https://akces-hub.adriangauza.pl'
```

⚠ **Plugin PHP wymaga 1-linijkowego update** w `class-akces-order-webhook.php`:

```php
private const HUB_ENDPOINT = '/api/v1/sklepakces/orders';  // PRZED: '/api/v1/orders'
```

Bez tego plugin wysyła na zajęty namespace `api_v1` → 401.

### 4. Database schema

Auto-tworzy się przy app start (`init_sklepakces_schema()` w `app.py` po `init_db()`).
Idempotent `CREATE TABLE IF NOT EXISTS` — kolejne uruchomienia no-op.

⚠ **Backup przed pierwszym deploy** na production:

```bash
cp ~/akces-hub/akces_hub.db ~/akces-hub/akces_hub.db.before-sklepakces-$(date +%Y-%m-%d)
```

### 5. Run

```bash
# Development (foreground):
cd ~/akces-hub
python app.py
# → [OK] Sklepakces integration zarejestrowane (prefix /api/v1/sklepakces)

# Production (systemd):
sudo systemctl restart akces-hub
sudo journalctl -u akces-hub -f | grep -i sklepakces
```

---

## Testing

### Pytest (33 tests)

```bash
cd ~/akces-hub
python -m pytest tests/test_sklepakces/ -v
# 33 passed, 1 skipped (TTL test gdy Redis fallback), ~5s
```

**Critical test:** `test_php_python_parity_reference_vector` — bit-perfect match z PHP
`Akces_Hmac_Test::test_sign_canonical_string_format`. Gdyby ten fail'ował, plugin
PHP nie mógłby dogadać się z Hub'em.

### E2E z plugin'em WP

```bash
# 1. Plugin (LocalWP Site Shell):
wp akces test cleanup
wp akces test smoke

# Expected (po skonfigurowanym Hub URL + matching secret):
#   ✓ webhook         PASS (status=sent)
# zamiast wcześniejszego:
#   ✓ webhook         PASS (status=retry-1)  ← Hub down, retry queue
```

### Inspect db

```bash
sqlite3 ~/akces-hub/akces_hub.db ".schema sklepakces_*"
sqlite3 ~/akces-hub/akces_hub.db "SELECT * FROM sklepakces_orders ORDER BY id DESC LIMIT 5"
sqlite3 ~/akces-hub/akces_hub.db "SELECT * FROM sklepakces_webhook_log ORDER BY id DESC LIMIT 10"
```

### Manual curl test

```bash
# Health (no HMAC):
curl http://localhost:5000/api/v1/sklepakces/health

# Signed POST (Python helper):
python3 -c "
import hmac, hashlib, json, time, urllib.request
SECRET = 'paste-same-secret'
body = json.dumps({'order_id':1,'total':10.0,'customer':{'email':'a@b.com'},'items':[{'sku':'X'}]})
ts = int(time.time())
sig = hmac.new(SECRET.encode(), f'POST:/api/v1/sklepakces/orders:{ts}:{body}'.encode(), hashlib.sha256).hexdigest()
req = urllib.request.Request('http://localhost:5000/api/v1/sklepakces/orders', data=body.encode(),
    headers={'Content-Type':'application/json','X-Akces-Timestamp':str(ts),'X-Akces-Signature':sig})
print(urllib.request.urlopen(req).read().decode())
"
```

---

## Database schema

### `sklepakces_orders`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `wc_order_id` | INTEGER | WC order ID po stronie WP |
| `order_number` | TEXT | WC display number ("1042") |
| `status` | TEXT | processing / completed / refunded |
| `total` | DECIMAL | grand total z VAT |
| `currency` | TEXT | PLN |
| `customer_email` | TEXT | indexed dla audit |
| `customer_data` / `items_data` / `payment_data` / `metadata` | TEXT | JSON blobs |
| `signature_nonce` | TEXT UNIQUE | HMAC sig z X-Akces-Signature — idempotency key |
| `received_at` | DATETIME | |
| `processed_at` / `paletomat_allocated_at` | DATETIME | Future Faza 4 |

### `sklepakces_products`
| Column | Type | Notes |
|---|---|---|
| `wc_product_id` | INTEGER UNIQUE | upsert key |
| `sku` / `name` / `regular_price` / `sale_price` / `stock_quantity` | typowe |
| `product_data` | TEXT | full payload JSON |
| `gpsr_data` | TEXT | JSON z manufacturer/responsible/warnings/model/origin |
| `allegro_listed_at` / `allegro_listing_id` | future Faza 4 |

### `sklepakces_stock_log`
Audit trail każdej zmiany stocku (old → new + reason).

### `sklepakces_webhook_log`
Każdy webhook call (success/error/duration_ms/client_ip).

---

## Files inventory

```
modules/sklepakces_hmac.py          ~232 linii  HMAC sign/verify + @require_sklepakces_hmac
modules/redis_nonce_cache.py        ~161 linii  Redis singleton + in-memory fallback
modules/sklepakces_telegram.py      ~128 linii  Alerty z [HUB] prefix + throttle 10/min
modules/sklepakces_webhook.py       ~164 linii  POST /orders handler
modules/sklepakces_products.py      ~123 linii  POST /products handler (INSERT/UPDATE)
modules/sklepakces_stock.py         ~115 linii  POST /stock_sync + GET /inventory/<sku>
modules/sklepakces_blueprint.py     ~173 linii  Flask Blueprint + schema migrations
app.py (edit)                       +11 linii   register_blueprint po api_v1
requirements.txt (edit)             +2 linii    redis>=5.0.0
tests/test_sklepakces/conftest.py    ~95 linii  minimal_app + signed_request fixtures
tests/test_sklepakces/test_hmac.py   ~120 linii 12 unit tests
tests/test_sklepakces/test_nonce_cache.py ~80 linii 6 tests
tests/test_sklepakces/test_blueprint.py ~210 linii 16 E2E tests
README_SKLEPAKCES.md                ~280 linii  ten dokument
config/.env.sklepakces.example       ~50 linii   env template

TOTAL: ~1942 linii w 12 nowych plikach + 2 edit
```

---

## Migration plan: Hub stub → real Hub

| Step | Action |
|---|---|
| 1 | Deploy do `~/akces-hub-dev/` (lub branch `feature/sklepakces-integration`) |
| 2 | Update plugin PHP `HUB_ENDPOINT` constant + restart |
| 3 | Plugin config: `hub_url` → dev URL (np. `http://192.168.100.200:5000`) |
| 4 | E2E test: `wp akces test smoke` → webhook PASS (sent) |
| 5 | 24h monitoring — Telegram alerts + logs |
| 6 | Merge `feature/sklepakces-integration` → `main` → push origin |
| 7 | Production: `git pull && sudo systemctl restart akces-hub` |
| 8 | Plugin config: `hub_url` → prod URL (Cloudflare Tunnel) |
| 9 | E2E test ponownie z production URL |
| 10 | Remove tools/hub-stub-server/ z plugin'a (lub keep jako dev tool) |

---

## Troubleshooting

**"HMAC verification failed: Signature mismatch"** — `sklepakces_hmac_secret`
w Hub config NIE matches `akces_hub_hmac_secret` w plugin WP option.

```bash
# Sprawdz:
python -c "from modules.database import get_config; print(repr(get_config('sklepakces_hmac_secret')))"
wp option get akces_hub_hmac_secret  # w LocalWP Site Shell
```

Oba muszą być **dokładnie** te same (włącznie z whitespace).

**"Timestamp outside 300s window"** — zegar RPi5 nieskonfigurowany.

```bash
timedatectl status                    # czy NTP synchronized?
sudo systemctl restart systemd-timesyncd
date -u; ssh sklep.lh.pl 'date -u'    # porównaj czasy
```

**"akces_replay_detected"** — sig już użyty w 5-min oknie. Możliwe że:
- Plugin retry queue wysyła same body 2× w 24h (signature nonce TTL)
- Klient testuje (`wp akces test smoke`) — clear cache: `redis-cli del 'akces:sklepakces:nonce:*'`

**Redis down** — Telegram alert `[HUB] ⚠️ Redis connection lost`. Sprawdz:

```bash
sudo systemctl status redis-server
redis-cli ping  # → PONG (lub error)
sudo systemctl restart redis-server
```

Fallback in-memory działa, ale tylko single-process — multi-worker Gunicorn
może nie wykryć replay między workerami. Naprawić Redis ASAP.

---

## Contact

- **Owner:** Adrian Gauza
- **Email:** kontakt@sklepakces.pl
- **Telegram:** [HUB] alerts wysyłane na chat z plugin'owym [SHOP]
- **Repo plugin PHP:** [Trupson2/sklepakces](https://github.com/Trupson2)
