# AKCES HUB — PLAN IMPLEMENTACJI
## Moduł 1: Photo Daemon | Moduł 2: Winning Products Analyzer

---

## 1. ANALIZA ISTNIEJĄCEGO SYSTEMU

### Co już mamy (ważne dla integracji):
| Zasób | Plik | Uwagi |
|---|---|---|
| SQLite DB | `modules/database.py` | `produkty` tabela z `images` (JSON array), `meta_title`, `status` |
| Allegro OAuth2 | `modules/allegro_api.py` | token refresh, `allegro_request()`, `detect_category_id()` |
| Image enhancer | `modules/image_enhancer.py` | już istnieje — może być reużyty |
| Konfiguracja | `modules/database.py` → `config` table | `get_config()` / `set_config()` |
| Blueprinty Flask | `app.py` | `analityka_bp`, `magazynier_bp` itd. — wzorzec dodawania modułów |
| **ComfyUI** | RTX 3070 PC | HTTP API na porcie 8188 — workflow JSON dla bg removal / inpainting |

### Kluczowe decyzje architektoniczne:
1. **Photo daemon** → osobny folder `photo_daemon/` (uruchamiany na Pi, **nie** jako Blueprint)
2. **Winning products** → integruje się jako Blueprint `winning_bp` do istniejącego Akces Hub
3. **Wspólna DB** → `akces_hub.db` — obie funkcje dodają swoje tabele przez `init_db()`-style migration
4. **Allegro client** → NIE duplikujemy kodu — winning products reużywa `modules/allegro_api.py`

---

## 2. MODUŁ 1 — PHOTO DAEMON

### Cel
Autonomiczny daemon na Pi 5: obserwuje folder INBOX, przetwarza zdjęcia, wysyła do rembg (RTX/VPS), generuje warianty, aktualizuje DB produktu.

### Struktura plików
```
photo_daemon/
├── config.yaml               # konfiguracja całego modułu
├── config.py                 # ładowanie config.yaml
├── db_utils.py               # init tabel, CRUD photo_jobs + processed_photos
├── image_utils.py            # Pillow: rotate EXIF, crop, resize, brightness/contrast
├── external_api_client.py    # HTTP client → rembg_service.py (VPS/RTX)
├── photo_watcher.py          # skanuje INBOX/, rejestruje joby
├── photo_worker.py           # przetwarza joby (new → processing → done/error)
├── status_app.py             # Flask panel: /  /job/<id>  /health
├── storage/
│   ├── originals/            # zarchiwizowane oryginały
│   ├── processed/            # <sku_lub_pid>/<variant>.jpg
│   └── workdir/              # tymczasowe pliki robocze
└── README.md
```

### Schemat DB (dodawane do akces_hub.db)

```sql
CREATE TABLE IF NOT EXISTS photo_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL,
    work_path    TEXT,
    product_id   INTEGER NULL REFERENCES produkty(id),
    sku          TEXT NULL,
    status       TEXT NOT NULL DEFAULT 'new',  -- new|processing|done|error
    error_msg    TEXT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES photo_jobs(id),
    product_id  INTEGER NULL,
    sku         TEXT NULL,
    variant     TEXT NOT NULL,  -- allegro_main|vinted|thumb
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_processed_photos_product
    ON processed_photos(product_id, variant);

-- Kolumna w produkty (jeśli nie istnieje):
-- ALTER TABLE produkty ADD COLUMN images_ready INTEGER DEFAULT 0;
-- ALTER TABLE produkty ADD COLUMN photo_job_id INTEGER NULL;
```

### Pipeline przetwarzania (photo_worker.py)

```
INBOX/SKU_1.jpg
    │
    ▼
[photo_watcher] ─→ photo_jobs (status='new')
                    original → storage/originals/SKU_1_uuid.jpg
    │
    ▼
[photo_worker]
    ├─ 1. EXIF auto-rotate (image_utils.fix_orientation)
    ├─ 2. Crop to aspect ratio (configurable, e.g. 1:1)
    ├─ 3. Brightness/contrast tweak (pillow ImageEnhance)
    ├─ 4. POST → ComfyUI API (external_api_client)
    │        workflow: LoadImage → BiRefNet/RMBG bg removal → SaveImage
    │        jeśli błąd / timeout → fallback (oryginał bez tła)
    └─ 5. Generuj warianty:
          ├─ allegro_main: 1200×1200 JPEG q=90
          ├─ vinted:        800×800  JPEG q=85
          └─ thumb:         300×300  JPEG q=80
          Zapis: storage/processed/<sku>/<variant>.jpg
    │
    ▼
processed_photos (3 rows per job)
produkty.images = JSON array z ścieżkami
produkty.images_ready = 1 (jeśli ≥ REQUIRED_PHOTO_COUNT)
```

### config.yaml (sekcja photo_daemon)

```yaml
photo_daemon:
  db_path: "/home/pi/akces-hub/akces_hub.db"
  inbox_path: "/home/pi/akces-hub/photo_daemon/storage/inbox"
  originals_path: "/home/pi/akces-hub/photo_daemon/storage/originals"
  workdir_path: "/home/pi/akces-hub/photo_daemon/storage/workdir"
  processed_base_path: "/home/pi/akces-hub/photo_daemon/storage/processed"
  status_port: 5051
  max_jobs_per_run: 10
  required_photo_count: 1

  external_api:
    type: "comfyui"                     # comfyui | mock
    url: "http://192.168.1.100:8188"   # ComfyUI na RTX 3070 PC
    workflow_file: "workflows/bg_remove.json"  # ścieżka do workflow JSON
    output_node_id: "9"                # ID noda SaveImage w workflow
    timeout_s: 60
    mock_mode: false                    # true = pomiń, użyj oryginału

  processing:
    target_aspect_ratio: "1:1"
    brightness: 1.05
    contrast: 1.10
    allegro_size: [1200, 1200]
    vinted_size:  [800, 800]
    thumb_size:   [300, 300]
    jpeg_quality: 90
```

### Konwencja nazw plików w INBOX

Pattern: `{SKU}_{numer}.jpg` lub `{SKU}.jpg` lub `{product_id}_{numer}.jpg`

Przykłady:
- `DYSON-V11_1.jpg` → sku=`DYSON-V11`
- `1234_front.jpg` → próba match product_id=1234 z DB
- `foto.jpg` → job bez powiązania z produktem (manual assign)

### HTTP Panel (status_app.py)

```
GET /          → HTML dashboard (statystyki + ostatnie 50 jobów)
GET /job/<id>  → szczegóły joba + lista wariantów
GET /health    → {"status":"ok","jobs_new":N,"jobs_error":N}
GET /api/jobs  → JSON (do integracji z Akces Hub panelem)
```

### ComfyUI API flow (external_api_client.py)

ComfyUI nie ma prostego "prześlij zdjęcie → dostań wynik". Flow jest asynchroniczny:

```
1. POST /upload/image          → prześlij plik, dostań filename
2. POST /prompt                → wyślij workflow JSON z tym filename, dostań prompt_id
3. GET  /history/{prompt_id}   → polling aż status != 'pending'
                                  lub WebSocket ws://host:8188/ws dla real-time
4. GET  /view?filename=X&type=output  → pobierz wynikowy plik PNG
```

**Workflow JSON** (`workflows/bg_remove.json`):
```json
{
  "1": { "class_type": "LoadImage", "inputs": { "image": "{INPUT_FILENAME}" } },
  "2": { "class_type": "BRIA_RMBG_ModelLoader", "inputs": {} },
  "3": { "class_type": "BRIA_RMBG", "inputs": { "model": ["2",0], "image": ["1",0] } },
  "9": { "class_type": "SaveImage", "inputs": { "images": ["3",0], "filename_prefix": "{OUTPUT_PREFIX}" } }
}
```

*Alternatywne nody bg removal w ComfyUI: `BiRefNet`, `InSeg`, `RemBG` — konfigurowalny workflow_file.*

### Deployment na Pi (cron + systemd)

```
# crontab -e
* * * * * cd /home/pi/akces-hub/photo_daemon && python photo_watcher.py
* * * * * cd /home/pi/akces-hub/photo_daemon && python photo_worker.py

# status panel jako systemd service
[Unit]
Description=AKCES Photo Status Panel
[Service]
ExecStart=/usr/bin/python3 /home/pi/akces-hub/photo_daemon/status_app.py
Restart=always
[Install]
WantedBy=multi-user.target
```

---

## 3. MODUŁ 2 — WINNING PRODUCTS ANALYZER

### Cel
Jeden klik w Akces Hub → analiza bestsellerów Allegro → score → wyniki w tabeli "Okazje".

### Struktura plików (integracja z istniejącym systemem)

```
modules/
├── winning_products.py    # Flask Blueprint winning_bp  ← NOWY
├── winning_analyzer.py    # Orchestrator run_scan()     ← NOWY
├── winning_scoring.py     # Algorytm scoringowy         ← NOWY
├── akces_data.py          # Odczyt z akces_hub.db       ← NOWY
└── allegro_api.py         # ISTNIEJĄCY — reużywamy

templates/
└── winning_products.html  # HTML panel z tabelą i przyciskiem ← NOWY
```

### Schemat DB

```sql
CREATE TABLE IF NOT EXISTS winning_products (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source             TEXT NOT NULL DEFAULT 'allegro',
    external_id        TEXT,
    name               TEXT,
    category           TEXT,
    category_id        TEXT,
    marketplace_url    TEXT,
    my_product_id      INTEGER NULL REFERENCES produkty(id),
    my_sku             TEXT NULL,
    est_price          REAL NULL,
    est_monthly_sales  REAL NULL,
    est_margin         REAL NULL,
    trend_score        REAL NULL,
    competition_score  REAL NULL,
    opportunity_score  REAL NULL,
    notes              TEXT NULL,
    batch_id           TEXT NULL,
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_winning_opportunity
    ON winning_products(opportunity_score DESC);

CREATE TABLE IF NOT EXISTS winning_products_meta (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id       TEXT UNIQUE,
    started_at     TEXT,
    finished_at    TEXT,
    status         TEXT,  -- running|done|error
    categories_scanned TEXT,
    products_found INTEGER DEFAULT 0,
    error_msg      TEXT NULL
);
```

### Algorytm scoringowy (winning_scoring.py)

#### trend_score (0–1)
```
Sygnały (ważone):
  + liczba aktywnych ofert w kategorii  → popyt
  + sprzedaż wnioskowana z "sold count" (jeśli Allegro udostępni)
  + cena rynkowa stabilna / rosnąca
  + kategoria w "top performing" u usera (historyczne dane sprzedaży)
Normalizacja: log-scale + min-max per batch
```

#### competition_score (0–1, wyżej = mocniejsza konkurencja)
```
  + liczba ofert w tej samej kategorii/podkategorii
  + spread cenowy (mała wariancja = price war)
  + procent ofert z "fulfillment" (Allegro Smart!)
```

#### opportunity_score (0–1)
```
opportunity = (trend_score * w_trend
             + (1 - competition_score) * w_comp
             + category_fit * w_fit
             + margin_potential * w_margin)
             / (w_trend + w_comp + w_fit + w_margin)

Domyślne wagi (konfigurowalane):
  w_trend  = 0.35
  w_comp   = 0.30
  w_fit    = 0.20
  w_margin = 0.15
```

**category_fit**: czy user ma produkty / historię sprzedaży w tej kategorii?
**margin_potential**: `(est_price - est_cost) / est_price` gdzie est_cost = średni COGS z palet usera

### Allegro API flow (reużywamy modules/allegro_api.py)

```
Nowe funkcje dodawane do istniejącego allegro_api.py LUB wywoływane przez winning_analyzer.py:

1. allegro_request('GET', '/categories/{id}/bestselling-offers')
   → bestsellery per kategoria

2. allegro_request('GET', '/offers/listing', params={
       'category.id': cat_id,
       'sort': '-popularity',
       'limit': 50
   })
   → popularne oferty w kategorii

3. allegro_request('GET', '/sale/offer-stats', ...)
   → statystyki własnych ofert (do obliczenia category_fit)
```

*Uwaga: Allegro nie zawsze udostępnia dokładne "sold count" w public API. Używamy proxy: liczba obserwujących, cena, liczba aktywnych ofert w kategorii jako sygnały popytu.*

### Web endpoint (winning_products.py)

```python
# Blueprint: winning_bp, prefix /api/analityka/winning

POST /refresh
  Body: {} (opcjonalnie {"categories": ["123","456"]})
  Response: {
    "batch_id": "...",
    "products_found": 42,
    "duration_s": 8.3,
    "top_3": [{"name": "...", "opportunity_score": 0.87}, ...]
  }
  Rate-limit: jeśli ostatni scan < 30 min → 429 z info

GET /list
  Params: ?limit=50&offset=0&min_score=0.3
  Response: {
    "items": [...],
    "total": N,
    "last_run": "2026-03-29T12:00:00"
  }

GET /meta
  Response: ostatnie 5 runów z metadanymi
```

### Panel HTML (templates/winning_products.html)

```
┌─────────────────────────────────────────┐
│  🏆 WINNING PRODUCTS — OKAZJE           │
│  Ostatni scan: 2026-03-29 12:00         │
│  [🔄 Przelicz teraz]  ← jeden przycisk  │
├────┬──────────────────┬────┬────┬───────┤
│  # │ Produkt          │ T  │ K  │ OPP ↓ │
├────┼──────────────────┼────┼────┼───────┤
│  1 │ Kamera EZVIZ 4K  │.85 │.40 │ 0.87  │
│  2 │ Poduszka ortoped │.72 │.55 │ 0.71  │
│... │ ...              │... │... │ ...   │
└────┴──────────────────┴────┴────┴───────┘
T = trend_score, K = competition_score
```

### Konfiguracja (sekcja winning_products w config DB)

Zapisywane w `config` table (get_config/set_config):

```
winning_categories = "["258682","257993","...]"  ← JSON array ID kategorii Allegro
winning_min_price = "20"
winning_max_price = "2000"
winning_max_results = "50"
winning_min_trend_score = "0.3"
winning_min_opportunity_score = "0.3"
winning_weights = '{"trend":0.35,"comp":0.30,"fit":0.20,"margin":0.15}'
winning_last_run = "2026-03-29T12:00:00"
winning_cooldown_minutes = "30"
```

---

## 4. PLAN WDROŻENIA (STEPS)

### STEP 2 — Scaffold + DB migrations
- [ ] Struktura folderów `photo_daemon/`
- [ ] `photo_daemon/db_utils.py` — `init_tables()` tworzy `photo_jobs` + `processed_photos`
- [ ] `photo_daemon/config.py` — ładuje `config.yaml`
- [ ] `modules/database.py` → dodaj `winning_products` + `winning_products_meta` do `init_db()`

### STEP 3 — Photo daemon
- [ ] `image_utils.py` — EXIF rotate, crop, resize, enhance
- [ ] `external_api_client.py` — HTTP POST do rembg (+ mock mode)
- [ ] `photo_watcher.py` — INBOX scanner + job creation
- [ ] `photo_worker.py` — pipeline przetwarzania
- [ ] `status_app.py` — Flask panel
- [ ] Test end-to-end z sample.jpg

### STEP 4 — Winning products
- [ ] `modules/akces_data.py` — get_my_products, get_sales_stats, get_margins
- [ ] `modules/winning_scoring.py` — trend/competition/opportunity scores
- [ ] `modules/winning_analyzer.py` — orchestrator run_scan()
- [ ] `modules/winning_products.py` — Flask Blueprint + endpoints
- [ ] `templates/winning_products.html` — panel z przyciskiem
- [ ] Rejestracja blueprintu w `app.py`

### STEP 5 — Dokumentacja
- [ ] `photo_daemon/README.md` — deploy na Pi, cron, RTX
- [ ] Sekcja w głównym README — Allegro credentials, winning products setup

---

## 5. ZALEŻNOŚCI (requirements)

### Photo daemon (Pi)
```
Pillow>=10.0
PyYAML>=6.0
Flask>=3.0
requests>=2.31
watchdog>=3.0  # opcjonalnie (lub prosty poll)
```

### Winning products (Akces Hub, już zainstalowane)
```
requests  # już jest
Flask     # już jest
# NIE potrzeba dodatkowych
```

---

## 6. RYZYKA I DECYZJE

| Ryzyko | Decyzja |
|--------|---------|
| Allegro API nie zwraca "sold count" | Proxy: liczba obserwujących + rank w wyszukiwarce |
| ComfyUI niedostępny / timeout | `mock_mode=true` lub fallback → oryginalny obraz |
| ComfyUI workflow niezgodny | `workflow_file` konfigurowalny — user podmienia JSON |
| Długi czas generacji (RTX 3070 ~3-10s/img) | Polling z timeout_s=60, async queue w workerze |
| Pi offline podczas photo processing | Cron + idempotentny worker (safe to re-run) |
| Długi scan winning products (>30s) | Background thread w Flask z polling endpoint |
| Kolizja z istniejącym allegro_api.py | NIE modyfikujemy — tylko wywołujemy `allegro_request()` |

---

## 7. CO NIE JEST W SCOPE (MVP)

- GUI do ręcznego przypisywania jobów do produktów (MVP: auto przez filename)
- Multi-tenant / per-user isolation
- Ceneo / OLX integration w winning products (Allegro tylko w MVP)
- Real-time WebSocket updates w status panelu
- Automatyczne zamawianie produktów z winning list

---

*Wersja planu: 1.0 — 2026-03-29*
*Gotowy do implementacji po akceptacji.*
