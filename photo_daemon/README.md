# AKCES Hub — Photo Daemon

Autonomiczny daemon na Raspberry Pi 5 do automatycznego przetwarzania zdjęć produktowych.

## Architektura

```
INBOX/ (drop zdjęcia tutaj)
    │
    ▼
photo_watcher.py   (cron co minutę)
    │ rejestruje joby w photo_jobs
    ▼
photo_worker.py    (cron co minutę)
    │ EXIF rotate → crop → enhance → ComfyUI bg removal → warianty
    ▼
storage/processed/{sku}/
    ├── allegro_main.jpg  (1200x1200)
    ├── vinted.jpg        (800x800)
    └── thumb.jpg         (300x300)
    │
    ▼
akces_hub.db: processed_photos, produkty.images
    │
    ▼
status_app.py  (http://pi:5051)
```

## 1. Instalacja na Pi

```bash
# Zainstaluj zależności
pip install Pillow PyYAML Flask requests

# Pobierz kod
git clone ... lub skopiuj folder photo_daemon/

# Stwórz katalogi storage
mkdir -p storage/inbox storage/originals storage/workdir storage/processed
```

## 2. Konfiguracja config.yaml

Edytuj `photo_daemon/config.yaml`:

```yaml
photo_daemon:
  db_path: "/home/pi/akces-hub/akces_hub.db"
  inbox_path: "/home/pi/akces-hub/photo_daemon/storage/inbox"
  originals_path: "/home/pi/akces-hub/photo_daemon/storage/originals"
  workdir_path: "/home/pi/akces-hub/photo_daemon/storage/workdir"
  processed_base_path: "/home/pi/akces-hub/photo_daemon/storage/processed"

  external_api:
    url: "http://192.168.1.100:8188"   # IP komputera z ComfyUI
    mock_mode: false                    # false = prawdziwe bg removal

  processing:
    target_aspect_ratio: "1:1"
    brightness: 1.05
    contrast: 1.10
```

## 3. Cron — automatyczne uruchamianie

```bash
crontab -e
```

Dodaj linie:
```
# Photo Daemon — skanowanie INBOX co minutę
* * * * * cd /home/pi/akces-hub/photo_daemon && python photo_watcher.py >> /var/log/photo_watcher.log 2>&1

# Photo Daemon — przetwarzanie zleceń co minutę
* * * * * cd /home/pi/akces-hub/photo_daemon && python photo_worker.py >> /var/log/photo_worker.log 2>&1
```

## 4. Systemd — status panel jako serwis

Utwórz `/etc/systemd/system/akces-photo-status.service`:

```ini
[Unit]
Description=AKCES Photo Daemon Status Panel
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/akces-hub/photo_daemon
ExecStart=/usr/bin/python3 status_app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Aktywuj serwis:
```bash
sudo systemctl daemon-reload
sudo systemctl enable akces-photo-status
sudo systemctl start akces-photo-status
sudo systemctl status akces-photo-status
```

Panel dostępny pod: `http://192.168.1.pi_ip:5051`

## 5. Konfiguracja ComfyUI na PC z RTX

1. Zainstaluj ComfyUI: `https://github.com/comfyanonymous/ComfyUI`
2. Zainstaluj ComfyUI-BiRefNet-ZHO:
   ```
   cd ComfyUI/custom_nodes
   git clone https://github.com/ZHO-ZHO-ZHO/ComfyUI-BiRefNet-ZHO
   pip install -r ComfyUI-BiRefNet-ZHO/requirements.txt
   ```
3. Uruchom ComfyUI: `python main.py --listen 0.0.0.0 --port 8188`
4. Zaktualizuj `config.yaml`: ustaw IP PC i `mock_mode: false`

### Workflow bg_remove.json

Plik `workflows/bg_remove.json` zawiera:
- Node 1: `LoadImage` — wczytuje przesłany plik
- Node 2: `BiRefNet_zho` — usuwa tło (BiRefNet model)
- Node 9: `SaveImage` — zapisuje wynik

Możesz podmienić workflow na własny jeśli używasz innych nodów.
Placeholdery w JSON: `{INPUT_FILENAME}` i `{OUTPUT_PREFIX}`.

## 6. Jak dodawać zdjęcia

### Konwencja nazw plików

Wrzuć zdjęcia do katalogu `INBOX/` z nazwą:
- `{SKU}_1.jpg` — np. `DYSON-V11_1.jpg`
- `{SKU}_front.jpg` — np. `DYSON-V11_front.jpg`
- `{SKU}.jpg` — np. `DYSON-V11.jpg`
- `{EAN}.jpg` — np. `5901234567890.jpg`

Daemon automatycznie:
1. Sparsuje SKU z nazwy pliku
2. Spróbuje znaleźć produkt w bazie (po EAN, nazwie, kodzie magazynowym)
3. Przetworzy zdjęcie i wygeneruje warianty
4. Zaktualizuje `produkty.images` (jeśli znaleziono produkt)

### Transfer zdjęć przez sieć

Z telefonu/PC możesz użyć:
```bash
# rsync/scp
scp zdjecia/*.jpg pi@192.168.1.X:/home/pi/akces-hub/photo_daemon/storage/inbox/

# Lub zamontuj INBOX przez Samba/NFS
```

## 7. Integracja z Akces Hub

Panel statusowy udostępnia API:

```
GET http://pi:5051/health       → {"status": "ok", "jobs_new": N, ...}
GET http://pi:5051/api/jobs     → JSON lista ostatnich jobów
GET http://pi:5051/             → HTML dashboard
GET http://pi:5051/job/<id>     → Szczegóły zlecenia
```

W Akces Hub możesz dodać widget pokazujący status:
```javascript
fetch('http://192.168.1.pi:5051/health')
  .then(r => r.json())
  .then(d => console.log(d));
```

## 8. Tryb mock (testowy)

W `config.yaml` ustaw `mock_mode: true` — daemon będzie kopiować zdjęcia bez ComfyUI.
Przydatne do testowania bez dostępu do PC z GPU.

## 9. Rozwiązywanie problemów

**Błąd "database is locked"**: baza jest zajęta przez Akces Hub. Normalny stan — worker ponowi próbę.

**ComfyUI timeout**: zwiększ `timeout_s` w konfiguracji lub sprawdź czy ComfyUI jest dostępne (`curl http://IP:8188/system_stats`).

**Zdjęcie nie zmienia się po przetworzeniu**: sprawdź logi workera, możliwe że bg removal wybrało inny node output. Edytuj `output_node_id` w config.yaml.

**Brak powiązania ze produktem**: sprawdź czy SKU w nazwie pliku pasuje do EAN, kodu_magazynowego lub nazwy produktu w bazie.
