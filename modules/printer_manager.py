"""
PrinterManager - Obsługa drukarki Niimbot B1 przez Bluetooth
============================================================
Moduł do drukowania etykiet produktowych z kodem QR.

Wymagane biblioteki:
    pip install bleak qrcode pillow python-barcode --break-system-packages

Niimbot B1 komunikuje się przez BLE (Bluetooth Low Energy).
"""

from __future__ import annotations  # Pozwala na forward references w type hints
print(">>> PRINTER_MANAGER LOADED v4 <<<")

import asyncio
import struct
import io
import base64
import sys
import platform
import threading
from datetime import datetime
from typing import Optional, Tuple, List, Callable, Any, TYPE_CHECKING
from dataclasses import dataclass
from enum import IntEnum

# Obsługa USB (Serial port)
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    serial = None

# UWAGA: NIE ustawiaj WindowsSelectorEventLoopPolicy!
# Bleak (BLE) na Windows wymaga domyślnego ProactorEventLoop.
# SelectorEventLoop łamie skanowanie Bluetooth.

# ============================================================
# DEDYKOWANY EVENT LOOP DLA BLE (bleak)
# Bleak wymaga jednego stałego event loop - nie może działać
# z wieloma loopami tworzonymi per-request w Flask
# ============================================================
_ble_loop: Optional[asyncio.AbstractEventLoop] = None
_ble_thread: Optional[threading.Thread] = None

def _start_ble_loop(loop: asyncio.AbstractEventLoop):
    """Uruchamia event loop w dedykowanym wątku"""
    asyncio.set_event_loop(loop)
    loop.run_forever()

def get_ble_loop() -> asyncio.AbstractEventLoop:
    """Zwraca dedykowany event loop dla BLE operacji"""
    global _ble_loop, _ble_thread
    if _ble_loop is None or _ble_loop.is_closed():
        # Na Windows musimy użyć ProactorEventLoop dla BLE (bleak wymaga)
        if sys.platform == 'win32':
            _ble_loop = asyncio.ProactorEventLoop()
        else:
            _ble_loop = asyncio.new_event_loop()
        _ble_thread = threading.Thread(target=_start_ble_loop, args=(_ble_loop,), daemon=True)
        _ble_thread.start()
    return _ble_loop

def run_ble_async(coro, timeout=60):
    """Uruchamia async coroutine w dedykowanym BLE event loop (thread-safe)"""
    loop = get_ble_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)

# Import funkcji pomocniczej
try:
    from .utils import pl_to_ascii
except ImportError:
    # Fallback jeśli import się nie uda
    def pl_to_ascii(text):
        if not text:
            return text
        replacements = {'ą':'a','ć':'c','ę':'e','ł':'l','ń':'n','ó':'o','ś':'s','ź':'z','ż':'z',
                        'Ą':'A','Ć':'C','Ę':'E','Ł':'L','Ń':'N','Ó':'O','Ś':'S','Ź':'Z','Ż':'Z'}
        for pl, ascii_char in replacements.items():
            text = text.replace(pl, ascii_char)
        return text

# Type checking imports (nie wykonują się w runtime)
if TYPE_CHECKING:
    from PIL import Image as PILImage

# Opcjonalne importy - graceful degradation
try:
    from bleak import BleakClient, BleakScanner
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False
    BleakClient = None
    BleakScanner = None

try:
    import qrcode
    from PIL import Image, ImageDraw, ImageFont
    IMAGING_AVAILABLE = True
except ImportError:
    IMAGING_AVAILABLE = False
    qrcode = None
    Image = None
    ImageDraw = None
    ImageFont = None

# Barcode - dla kodów kreskowych EAN
try:
    import barcode
    from barcode.writer import ImageWriter
    BARCODE_AVAILABLE = True
except ImportError:
    BARCODE_AVAILABLE = False
    barcode = None

# Niimprint - dedykowana biblioteka dla drukarek Niimbot
# UWAGA: niimprint wymaga Python 3.11 i może wymagać ręcznej instalacji:
#   py -3.11 -m pip install niimprint
NIIMPRINT_AVAILABLE = False
BluetoothTransport = None
SerialTransport = None  # USB
PrinterClient = None
NIIMPRINT_ERROR = None

try:
    from niimprint import BluetoothTransport, SerialTransport, PrinterClient
    NIIMPRINT_AVAILABLE = True
except ImportError as e:
    NIIMPRINT_ERROR = f"Brak biblioteki: {e}"
except Exception as e:
    NIIMPRINT_ERROR = f"Błąd importu: {e}"

def get_niimprint_status() -> dict:
    """Zwraca status biblioteki niimprint"""
    return {
        "available": NIIMPRINT_AVAILABLE,
        "error": NIIMPRINT_ERROR,
        "install_cmd": "py -3.11 -m pip install niimprint",
        "alt_install": "pip install git+https://github.com/AndBondStyle/niimprint.git"
    }


# ============================================================
# BleakTransport - BLE transport for niimprint
# ============================================================
# niimprint.BluetoothTransport uses classic BT (RFCOMM sockets)
# which does NOT work with BLE-only devices like Niimbot B1.
# BleakTransport bridges bleak (BLE) to niimprint's sync interface.
# ============================================================

class BleakTransport:
    """
    Transport BLE dla niimprint - używa bleak zamiast RFCOMM.
    Implementuje interfejs BaseTransport (read/write) aby
    niimprint.PrinterClient mógł komunikować się przez BLE.
    """

    def __init__(self, address: str):
        if not BLEAK_AVAILABLE:
            raise RuntimeError("Brak biblioteki bleak. Zainstaluj: pip install bleak")

        self._address = address
        self._client = None
        self._write_char = None
        self._notify_char = None
        self._write_char_obj = None  # BleakGATTCharacteristic object (handle)
        self._recv_buffer = bytearray()
        self._recv_event = threading.Event()
        self._loop = get_ble_loop()

        # Connect synchronously (blocks until done)
        # Timeout 30s bo auto-scan (8s) + connect (10s) + zapas
        self._run(self._async_connect(), timeout=30)

    def _run(self, coro, timeout=30):
        """Run async coroutine in the BLE event loop (thread-safe)"""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _async_connect(self):
        """Connect to BLE device and discover characteristics"""
        from bleak import BleakClient as _BC, BleakScanner as _BS

        # Attempt 1: connect directly to saved address
        try:
            print(f"  [BLE] Laczenie z {self._address}...")
            self._client = _BC(self._address)
            await self._client.connect(timeout=10.0)
            if self._client.is_connected:
                print(f"  [BLE] Polaczono z {self._address}")
        except Exception as e1:
            print(f"  [BLE] Adres {self._address} nie znaleziony: {e1}")
            print(f"  [BLE] Skanuje w poszukiwaniu Niimbot...")

            # Attempt 2: scan for any Niimbot device
            self._client = None
            devices = await _BS.discover(timeout=8)
            niimbot_dev = None
            for d in devices:
                name = (d.name or '').lower()
                if 'niimbot' in name or 'b1' in name or 'b21' in name or 'b18' in name or 'd11' in name or 'd110' in name:
                    niimbot_dev = d
                    break

            if not niimbot_dev:
                raise RuntimeError(
                    f"Drukarka {self._address} nie znaleziona i nie wykryto "
                    f"zadnego Niimbot w zasiegu. Sprawdz czy jest wlaczona."
                )

            new_addr = niimbot_dev.address
            print(f"  [BLE] Znaleziono: {niimbot_dev.name} @ {new_addr}")
            self._address = new_addr

            # Save new address to config
            try:
                from .database import set_config
                set_config('niimbot_bt_address', new_addr)
                print(f"  [BLE] Zapisano nowy adres BT: {new_addr}")
            except Exception:
                pass

            self._client = _BC(new_addr)
            await self._client.connect(timeout=10.0)

        if not self._client or not self._client.is_connected:
            raise RuntimeError(f"Nie udalo sie polaczyc z {self._address}")

        print(f"  [BLE] Polaczono OK: {self._address}")

        # Negotiate MTU — kluczowe! Domyślne MTU=23 ucina pakiety bitmap
        try:
            if hasattr(self._client, '_acquire_mtu'):
                await self._client._acquire_mtu()
                print(f"  [BLE] MTU po negocjacji: {self._client.mtu_size}")
            # Na bluez: request larger MTU
            if self._client.mtu_size < 60:
                try:
                    # bleak >= 0.21 na Linux
                    if hasattr(self._client, 'request_mtu'):
                        await self._client.request_mtu(247)
                        print(f"  [BLE] MTU po request: {self._client.mtu_size}")
                except Exception as mtu_err:
                    print(f"  [BLE] MTU request failed: {mtu_err}")
        except Exception as e:
            print(f"  [BLE] MTU acquire: {e}")

        # Discover write and notify characteristics
        # First pass: look specifically for Niimbot service UUID
        niimbot_svc = NIIMBOT_SERVICE_UUID.lower()
        niimbot_tx = NIIMBOT_CHAR_TX_UUID.lower()
        fallback_write = None
        fallback_notify = None

        for service in self._client.services:
            svc_uuid = str(service.uuid).lower()
            is_niimbot_svc = niimbot_svc in svc_uuid
            for char in service.characteristics:
                char_uuid = str(char.uuid).lower()
                props = char.properties
                # Exact match on Niimbot TX UUID
                if niimbot_tx in char_uuid:
                    self._write_char = char.uuid
                    self._write_char_obj = char  # GATT object z prawidłowym handle
                    print(f"  [EDIT] Niimbot TX: {char.uuid} handle={char.handle} props={props}")
                # Notify in Niimbot service
                if is_niimbot_svc and "notify" in props and not self._notify_char:
                    self._notify_char = char.uuid
                    print(f"  [SATE] Niimbot RX: {char.uuid} props={props}")
                # Fallbacks (any writable/notify char)
                if ("write-without-response" in props or "write" in props) and not fallback_write:
                    fallback_write = char.uuid
                if "notify" in props and not fallback_notify:
                    fallback_notify = char.uuid

        # Use fallbacks only if Niimbot-specific not found
        if not self._write_char and fallback_write:
            self._write_char = fallback_write
            print(f"  [EDIT] Write char (fallback): {fallback_write}")
        if not self._notify_char and fallback_notify:
            self._notify_char = fallback_notify
            print(f"  [SATE] Notify char (fallback): {fallback_notify}")

        if not self._write_char:
            raise RuntimeError("Nie znaleziono charakterystyki write na drukarce BLE")

        # Subscribe to notifications for receiving responses
        if self._notify_char:
            await self._client.start_notify(self._notify_char, self._on_notification)
            print(f"  [OK] Subskrypcja notyfikacji aktywna")
        else:
            print(f"  [WARN] Brak notify - odpowiedzi drukarki mogą nie dochodzić")

    def _on_notification(self, sender, data: bytearray):
        """Callback for BLE notifications (runs in BLE thread)"""
        self._recv_buffer.extend(data)
        self._recv_event.set()
        print(f"  [SATE] [NOTIF] {len(data)}B from {sender}: {bytes(data).hex()}", flush=True)

    def read(self, length: int) -> bytes:
        """Read data from BLE (niimprint calls this for responses)"""
        # Wait for notification data, with short timeout
        # niimprint's _transceive retries 6 times with 0.1s sleep,
        # so we don't need to block long here
        self._recv_event.wait(timeout=1.0)
        data = bytes(self._recv_buffer)
        self._recv_buffer.clear()
        self._recv_event.clear()
        return data

    def write(self, data: bytes):
        """Write data to BLE (niimprint calls this to send commands)"""
        self._write_count = getattr(self, '_write_count', 0) + 1
        if self._write_count <= 5 or self._write_count % 100 == 0:
            print(f"  >>> BLE.write #{self._write_count}, {len(data)}B: {data[:8].hex()}", flush=True)
        self._run(self._async_write(data), timeout=15)

    async def _async_write(self, data: bytes):
        """Send data via BLE — z pauzą po każdym write żeby bufor B1 nie pękł."""
        if not self._client or not self._client.is_connected:
            raise RuntimeError("BLE disconnected")

        # Log MTU i pierwsze pakiety
        if self._write_count == 1:
            mtu = self._client.mtu_size
            max_wr = getattr(self._client, 'max_write_without_response_size', 'N/A')
            print(f"  [BLE] MTU={mtu} max_write_no_resp={max_wr} "
                  f"pkt_size={len(data)}B "
                  f"char={getattr(self._write_char_obj, 'handle', '?')}", flush=True)

        # Log pierwszych 10 pakietów - pełny hex
        if self._write_count <= 10:
            print(f"  [PKT#{self._write_count}] {len(data)}B: {data.hex()}", flush=True)

        await self._client.write_gatt_char(self._write_char_obj, data, response=True)
        # Pauza po każdym write — bufor B1 jest mały, bez tego ucina wydruk
        # Komendy protokołu (krótkie) nie potrzebują dużej pauzy
        # Dane obrazu (długie, seria) — potrzebują więcej czasu
        if len(data) > 20:
            await asyncio.sleep(0.02)  # 20ms dla danych obrazu
        else:
            await asyncio.sleep(0.008)  # 8ms dla komend

    def send_image_chunked(self, packets_bytes, chunk_size=20, chunk_pause=0.3):
        """Wyślij obraz w porcjach z pauzą między porcjami.

        Drukarka B1 ma mały bufor (~20-25 linii). Trzeba wysyłać porcjami
        i czekać aż drukarka fizycznie wydrukuje każdą porcję.

        chunk_size: ile linii w jednej porcji (domyślnie 20)
        chunk_pause: pauza między porcjami w sekundach (drukarka drukuje)
        """
        self._run(
            self._async_send_chunked(packets_bytes, chunk_size, chunk_pause),
            timeout=120
        )

    async def _async_send_chunked(self, packets_bytes, chunk_size, chunk_pause):
        """Wysyła dane obrazu w małych porcjach z pauzami.

        Niimbot B1 ma mały bufor (~15-20 linii). Trzeba wysyłać porcjami
        i czekać aż drukarka fizycznie wydrukuje każdą porcję zanim
        wyślemy następną. Bez tego bufor się zapycha i wydruk jest ucięty.

        chunk_size: ile linii w jednej porcji (15-20 optymalne dla B1)
        chunk_pause: pauza między porcjami w sekundach (0.3-0.5s)
        """
        import time as _t
        t0 = _t.time()
        total = len(packets_bytes)
        errors = []

        # Log BLE state
        mtu = getattr(self._client, 'mtu_size', '?')
        connected = self._client.is_connected if self._client else False
        n_chunks = (total + chunk_size - 1) // chunk_size
        print(f"   [SEAR] BLE: connected={connected}, MTU={mtu}", flush=True)
        print(f"   [SEAR] SEND: {total} lines, {n_chunks} chunks of {chunk_size}, "
              f"pause={chunk_pause}s", flush=True)

        # Wysyłaj w porcjach
        for chunk_start in range(0, total, chunk_size):
            chunk_end = min(chunk_start + chunk_size, total)

            # Wyślij porcję linia po linii z response=True (niezawodne)
            for idx in range(chunk_start, chunk_end):
                try:
                    await self._client.write_gatt_char(
                        self._write_char_obj, packets_bytes[idx], response=True
                    )
                except Exception as e:
                    errors.append((idx, str(e)))
                    if len(errors) <= 3:
                        print(f"   [ERR] WRITE ERROR line {idx}: {e}", flush=True)

                # Mikro-pauza między pakietami w chunk (BLE flow control)
                await asyncio.sleep(0.005)

            # Pauza między porcjami — drukarka fizycznie drukuje te linie
            if chunk_end < total:
                await asyncio.sleep(chunk_pause)

            # Progress log
            elapsed = _t.time() - t0
            chunk_num = chunk_start // chunk_size + 1
            print(f"   [BAR_] Chunk {chunk_num}/{n_chunks}: sent {chunk_end}/{total} "
                  f"({elapsed:.1f}s, errors={len(errors)})", flush=True)

        elapsed = _t.time() - t0
        print(f"   [OK] SEND COMPLETE: {total} lines in {elapsed:.1f}s, "
              f"errors={len(errors)}", flush=True)

        # Poczekaj na notyfikacje
        await asyncio.sleep(0.2)
        if self._recv_buffer:
            notif_data = bytes(self._recv_buffer)
            self._recv_buffer.clear()
            self._recv_event.clear()
            print(f"   [SATE] NOTIF after send: {notif_data.hex()}", flush=True)

    def close(self):
        """Disconnect BLE"""
        try:
            if self._client and self._client.is_connected:
                self._run(self._client.disconnect(), timeout=5)
                print("[POWE] BleakTransport: rozłączono")
        except Exception as e:
            print(f"[WARN] BleakTransport close: {e}")


# ============================================================
# STAŁE PROTOKOŁU NIIMBOT
# ============================================================

class NiimbotCommand(IntEnum):
    """Komendy protokołu Niimbot"""
    GET_INFO = 0x40
    SET_LABEL_TYPE = 0x23
    SET_LABEL_DENSITY = 0x21
    START_PRINT = 0x01
    END_PRINT = 0x83
    START_PAGE = 0x03
    END_PAGE = 0xE3
    SET_DIMENSION = 0x13
    ALLOW_PRINT = 0x20
    GET_STATUS = 0xA3
    GET_PRINT_STATUS = 0xA4
    IMAGE_DATA = 0x85
    HEARTBEAT = 0xDC


# UUID dla komunikacji BLE z drukarką Niimbot
NIIMBOT_SERVICE_UUID = "e7810a71-73ae-499d-8c15-faa9aef0c3f2"
NIIMBOT_CHAR_TX_UUID = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"  # Do wysyłania
NIIMBOT_CHAR_RX_UUID = "00002a25-0000-1000-8000-00805f9b34fb"  # Do odbierania


@dataclass
class LabelConfig:
    """Konfiguracja etykiety"""
    width: int = 30       # Szerokość w mm (B1: 30mm wszerz głowicy = 240px @ 203dpi)
    height: int = 50      # Wysokość w mm (B1: 50mm kierunek podawania = 400px)
    density: int = 5      # Gęstość druku 1-5 (5=max ciemność)
    label_type: int = 1   # 1=gap, 2=black mark, 3=continuous
    dpi: int = 203        # Rozdzielczość drukarki


@dataclass
class ProductLabel:
    """Dane do wydruku na etykiecie"""
    nazwa: str                          # Nazwa produktu (skrócona)
    qr_data: str                        # Dane do kodu QR (URL aukcji lub ID)
    data_przyjecia: str = None          # Data przyjęcia (domyślnie dziś)
    lokalizacja: str = ""               # Regał/lokalizacja
    ean: str = ""                       # Opcjonalny kod EAN
    ilosc: int = 1                      # Ilość sztuk
    dostawca: str = ""                  # Dostawca (Jobalots, Warrington, etc.)
    data_zakupu: str = ""               # Data zakupu palety
    paleta: str = ""                    # Nazwa palety (np. "#2 Mix Kamerki")
    koszt_szt: float = 0               # Koszt zakupu brutto/szt z palety
    cena_allegro: float = 0            # Cena Allegro (jeśli wystawiony)
    kod_magazynowy: str = ""           # Kod magazynowy (MAG-XXXXX)
    stan_przyjecia: str = ""           # Stan z przyjęcia (Nowy, Jak nowy, Dobry, Uszkodzony, Zniszczony)
    
    def __post_init__(self):
        if not self.data_przyjecia:
            self.data_przyjecia = datetime.now().strftime('%d.%m.%Y')


class PrinterManager:
    """
    Manager drukarki Niimbot B1 przez Bluetooth Low Energy.
    
    Użycie:
        pm = PrinterManager()
        await pm.scan_printers()           # Skanuj dostępne drukarki
        await pm.connect("XX:XX:XX:XX")    # Połącz z drukarką
        await pm.print_label(ProductLabel(...))  # Drukuj etykietę
        await pm.disconnect()               # Rozłącz
    """
    
    def __init__(self, config: LabelConfig = None):
        self.config = config or LabelConfig()
        self.client: Optional[BleakClient] = None
        self.connected = False
        self.device_address = None
        self.device_name = None
        self._response_buffer = bytearray()
        self._response_event = asyncio.Event()
        
        # Obsługa USB
        self.usb_connection = None
        self.connection_type = 'bluetooth'  # 'bluetooth' lub 'usb'
        self.usb_port = None
        
    # ============================================================
    # SKANOWANIE I POŁĄCZENIE
    # ============================================================
    
    async def scan_printers(self, timeout: float = 10.0) -> List[dict]:
        """
        Skanuje dostępne drukarki Niimbot w pobliżu.
        
        Returns:
            Lista słowników z informacjami o drukarkach
        """
        if not BLEAK_AVAILABLE:
            return [{"error": "Biblioteka bleak nie jest zainstalowana"}]
        
        printers = []
        print(f"[SEAR] Skanowanie drukarek Bluetooth ({timeout}s)...")
        
        try:
            devices = await BleakScanner.discover(timeout=timeout)
            
            for device in devices:
                name = device.name or "Unknown"
                name_upper = name.upper()
                # Filtruj drukarki Niimbot - różne warianty nazw BLE
                # Niimbot B1 często reklamuje się jako "A1-XXXX", "B1-XXXX", "D11-XXXX" etc.
                niimbot_prefixes = ['NIIMBOT', 'B1', 'B21', 'B3S', 'D11', 'D110', 'A1-', 'B1-', 'B21-', 'B3S-', 'D11-', 'D110-']
                is_niimbot = any(name_upper.startswith(x) or x in name_upper for x in niimbot_prefixes)
                
                if is_niimbot:
                    printers.append({
                        "name": name,
                        "address": device.address,
                        "rssi": getattr(device, 'rssi', None)
                    })
                    print(f"  [OK] Znaleziono: {name} ({device.address})")
                    
            if not printers:
                print("  [WARN] Nie znaleziono drukarek Niimbot")
                # Pokaż wszystkie urządzenia BLE dla debugowania
                print("  [ASSI] Wszystkie urządzenia BLE:")
                for d in devices[:10]:
                    print(f"     - {d.name or 'N/A'}: {d.address}")
                    
        except Exception as e:
            print(f"[ERR] Błąd skanowania: {e}")
            printers.append({"error": str(e)})
            
        return printers
    
    async def connect(self, address: str = None) -> bool:
        """
        Łączy się z drukarką.
        
        Args:
            address: Adres MAC drukarki. Jeśli None, użyje pierwszej znalezionej.
        """
        if not BLEAK_AVAILABLE:
            print("[ERR] Biblioteka bleak nie jest zainstalowana")
            print("   Zainstaluj: pip install bleak --break-system-packages")
            return False
        
        if not address:
            print("[ERR] Nie podano adresu drukarki")
            return False
        
        # Zapisz adres dla niimprint
        self.device_address = address
            
        print(f"[LINK] Łączenie z drukarką: {address}...")
        
        try:
            self.client = BleakClient(address)
            await self.client.connect(timeout=15.0)
            
            if self.client.is_connected:
                self.connected = True
                
                # Subskrybuj notyfikacje
                try:
                    await self._setup_notifications()
                except Exception as e:
                    print(f"  [WARN] Nie udało się ustawić notyfikacji: {e}")
                
                # Pobierz info o drukarce
                try:
                    info = await self._get_printer_info()
                    self.device_name = info.get('name', 'Niimbot')
                except Exception as e:
                    self.device_name = 'Niimbot'
                    print(f"  [WARN] Nie udało się pobrać info: {e}")
                
                print(f"[OK] Połączono z {self.device_name}")
                return True
            else:
                print("[ERR] Nie udało się połączyć")
                return False
                
        except Exception as e:
            print(f"[ERR] Błąd połączenia: {e}")
            import traceback
            traceback.print_exc()
            self.connected = False
            return False
    
    async def disconnect(self):
        """Rozłącza drukarkę"""
        if self.client and self.connected:
            try:
                await self.client.disconnect()
                print("[POWE] Rozłączono z drukarką")
            except Exception as e:
                print(f"[WARN] Błąd rozłączania: {e}")
        self.connected = False
        self.client = None
        
    async def _setup_notifications(self):
        """Konfiguruje notyfikacje BLE"""
        def notification_handler(sender, data):
            self._response_buffer.extend(data)
            self._response_event.set()
            
        try:
            # Znajdź charakterystykę do odbierania
            for service in self.client.services:
                for char in service.characteristics:
                    if "notify" in char.properties:
                        await self.client.start_notify(char.uuid, notification_handler)
                        print(f"  [SATE] Subskrypcja notyfikacji: {char.uuid[:8]}...")
                        return
        except Exception as e:
            print(f"  [WARN] Nie można ustawić notyfikacji: {e}")
            
    # ============================================================
    # PROTOKÓŁ KOMUNIKACJI
    # ============================================================
    
    def _build_packet(self, command: int, data: bytes = b'') -> bytes:
        """
        Buduje pakiet protokołu Niimbot.
        
        Format: 0x55 0x55 CMD LEN_HI LEN_LO [DATA] CHECKSUM 0xAA 0xAA
        """
        length = len(data)
        packet = bytearray([0x55, 0x55, command, (length >> 8) & 0xFF, length & 0xFF])
        packet.extend(data)
        
        # Checksum XOR
        checksum = command ^ ((length >> 8) & 0xFF) ^ (length & 0xFF)
        for b in data:
            checksum ^= b
        packet.append(checksum & 0xFF)
        packet.extend([0xAA, 0xAA])
        
        return bytes(packet)
    
    async def _send_command(self, command: int, data: bytes = b'', wait_response: bool = True) -> Optional[bytes]:
        """Wysyła komendę do drukarki"""
        if not self.connected or not self.client:
            return None
            
        packet = self._build_packet(command, data)
        
        try:
            # Użyj konkretnej UUID Niimbot lub znajdź dynamicznie
            write_char = None
            
            # Szukaj Niimbot TX UUID we WSZYSTKICH serwisach (nie przerywaj po pierwszym write)
            fallback_char = None
            try:
                for service in self.client.services:
                    for char in service.characteristics:
                        char_uuid = str(char.uuid).lower()
                        if NIIMBOT_CHAR_TX_UUID.lower() in char_uuid:
                            write_char = char.uuid
                            break
                        if ("write" in char.properties or "write-without-response" in char.properties) and not fallback_char:
                            fallback_char = char.uuid
                    if write_char:
                        break
            except:
                pass

            if not write_char:
                write_char = fallback_char
                    
            if not write_char:
                print("[ERR] Nie znaleziono charakterystyki do zapisu")
                return None
            
            # Debug
            # print(f"  [UPLO] Wysyłam {len(packet)} bajtów do {write_char}")
                
            # Użyj write-without-response od razu (unika problemów z uprawnieniami Windows)
            try:
                await self.client.write_gatt_char(write_char, packet, response=False)
            except Exception as e:
                # Jeśli write-without-response nie działa, spróbuj z response
                try:
                    await self.client.write_gatt_char(write_char, packet, response=True)
                except:
                    print(f"[WARN]  Błąd zapisu BLE: {e}")
                    pass
            
            if wait_response:
                self._response_buffer.clear()
                self._response_event.clear()
                try:
                    await asyncio.wait_for(self._response_event.wait(), timeout=2.0)
                    return bytes(self._response_buffer)
                except asyncio.TimeoutError:
                    return None
            return b''
            
        except Exception as e:
            print(f"[ERR] Błąd wysyłania: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def _get_printer_info(self) -> dict:
        """Pobiera informacje o drukarce"""
        info = {"name": "Niimbot", "model": "Unknown"}
        
        try:
            response = await self._send_command(NiimbotCommand.GET_INFO, bytes([1]))
            if response and len(response) > 5:
                # Parsuj odpowiedź
                info['firmware'] = response[5:].decode('utf-8', errors='ignore').strip()
        except:
            pass
            
        return info
    
    # ============================================================
    # GENEROWANIE ETYKIETY
    # ============================================================
    
    def _generate_label_image(self, label: ProductLabel) -> Any:
        """
        Generuje pełną etykietę magazynową.
        Rozmiar: 576px szerokości × do 800px wysokości (szeroki format do druku).
        """
        if not IMAGING_AVAILABLE:
            raise RuntimeError("Biblioteki pillow/qrcode nie sa zainstalowane")

        from .utils import pl_to_ascii

        width_px = 576
        max_height = 800

        img = Image.new('L', (width_px, max_height), color=255)
        draw = ImageDraw.Draw(img)

        # --- Czcionki ---
        def _load_font(size, bold=False):
            paths_bold = [
                "C:/Windows/Fonts/arialbd.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            ]
            paths_normal = [
                "C:/Windows/Fonts/arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            ]
            for fp in (paths_bold if bold else paths_normal):
                try:
                    return ImageFont.truetype(fp, size)
                except:
                    continue
            return ImageFont.load_default()

        font_title = _load_font(24, bold=True)
        font_info = _load_font(18, bold=False)
        font_info_bold = _load_font(18, bold=True)
        font_small = _load_font(14, bold=False)
        font_kod = _load_font(16, bold=True)

        y = 10  # margin top

        # === NAZWA PRODUKTU — max 2 linie ===
        nazwa = pl_to_ascii(label.nazwa or 'Produkt')[:90]
        lines = []
        words = nazwa.split()
        current_line = ''
        for word in words:
            test = (current_line + ' ' + word).strip()
            bbox = draw.textbbox((0, 0), test, font=font_title)
            if bbox[2] - bbox[0] > width_px - 20:
                if current_line:
                    lines.append(current_line)
                current_line = word
            else:
                current_line = test
        if current_line:
            lines.append(current_line)
        lines = lines[:3]  # max 3 linie

        for line in lines:
            draw.text((10, y), line, font=font_title, fill=0)
            y += 28

        # Separator
        y += 4
        draw.line([(10, y), (width_px - 10, y)], fill=0, width=2)
        y += 8

        # === SEKCJA ŚRODKOWA: QR + INFO ===
        qr_size = 150
        qr_img = None
        info_x = qr_size + 20

        # Generuj QR — użyj kodu magazynowego jako danych QR
        qr_data = label.kod_magazynowy or label.qr_data or ''
        if qr_data:
            qr = qrcode.QRCode(
                version=None,
                box_size=5,
                border=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M
            )
            qr.add_data(qr_data)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white").convert('L')
            qr_w, qr_h = qr_img.size

            if qr_w != qr_size or qr_h != qr_size:
                qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST)

            img.paste(qr_img, (10, y))

        # Info obok QR
        info_y = y
        info_lines = []

        if label.lokalizacja:
            info_lines.append(('LOK: ' + pl_to_ascii(label.lokalizacja)[:22], font_info_bold))
        if label.ilosc and label.ilosc > 0:
            info_lines.append(('Szt: ' + str(label.ilosc), font_info))
        if label.stan_przyjecia:
            info_lines.append(('STAN: ' + pl_to_ascii(label.stan_przyjecia)[:18], font_info_bold))
        if label.paleta:
            pal_raw = pl_to_ascii(label.paleta).strip()
            pal_words = pal_raw.split()
            if len(pal_words) <= 3:
                pal_text = pal_raw
            elif pal_words[0].startswith('#'):
                pal_text = pal_words[0] + ' ' + pal_words[1]
            else:
                pal_text = ' '.join(pal_words[:2])
            info_lines.append(('PAL: ' + pal_text[:22], font_small))

        for text, font in info_lines[:6]:
            draw.text((info_x, info_y), text, font=font, fill=0)
            info_y += 24

        y += max(qr_size + 8, info_y - y + 8)

        # === EAN jako tekst (bez barcode - zajmuje za dużo miejsca) ===
        if label.ean and len(label.ean) >= 8:
            draw.text((10, y), f"EAN: {label.ean}", font=font_small, fill=0)
            y += 20

        # === DOLNA LINIA: kod magazynowy + data ===
        y += 6
        bottom_parts = []
        kod = label.kod_magazynowy or label.qr_data or ''
        if kod:
            bottom_parts.append(kod[:25])
        if label.data_przyjecia:
            bottom_parts.append(label.data_przyjecia)
        bottom_text = '  |  '.join(bottom_parts)
        if bottom_text:
            draw.text((10, y), bottom_text, font=font_small, fill=0)
            y += 20

        # Przytnij do faktycznej wysokości
        final_height = min(y + 10, max_height)
        img = img.crop((0, 0, width_px, final_height))

        print(f"   Label: {img.size[0]}x{img.size[1]}px, nazwa={label.nazwa[:30]}", flush=True)
        return img

    def _image_to_print_data(self, img: Any) -> bytes:
        """
        Konwertuje obraz do formatu druku Niimbot.
        
        Format: Każda linia to pakiet z danymi bitmapy.
        Niimbot B1 wymaga formatu: [line_num_hi, line_num_lo, ...bitmap_data...]
        """
        # Konwertuj do 1-bit mono
        img = img.convert('1')
        width, height = img.size
        pixels = img.load()
        
        data = bytearray()
        bytes_per_line = (width + 7) // 8
        
        # Każda linia osobno z numerem linii
        for y in range(height):
            # Numer linii (2 bajty, big-endian)
            line_data = bytearray([y >> 8, y & 0xFF])
            
            byte = 0
            bit_count = 0
            
            for x in range(width):
                # Piksel 0 = czarny (druk), 1 = biały (brak druku)
                if pixels[x, y] == 0:
                    byte |= (1 << (7 - bit_count))
                    
                bit_count += 1
                if bit_count == 8:
                    line_data.append(byte)
                    byte = 0
                    bit_count = 0
                    
            # Dopełnij ostatni bajt
            if bit_count > 0:
                line_data.append(byte)
                
            data.extend(line_data)
            
        return bytes(data)
    
    # ============================================================
    # DRUKOWANIE
    # ============================================================
    
    async def print_label(self, label: ProductLabel, copies: int = 1) -> bool:
        """
        Drukuje etykietę produktową.
        
        Args:
            label: Dane etykiety ProductLabel
            copies: Liczba kopii
            
        Returns:
            True jeśli sukces
        """
        # UWAGA: niimprint BluetoothTransport = klasyczny BT (RFCOMM)
        # Niimbot B1 = BLE (Bluetooth Low Energy) → wymaga bleak, NIE niimprint BT
        # niimprint SerialTransport = USB → działa OK
        
        # Ścieżka 1: USB przez niimprint (jeśli mamy USB port)
        if NIIMPRINT_AVAILABLE and self.connection_type == 'usb' and self.usb_port:
            print("[PUSH] Używam niimprint USB (SerialTransport)")
            return await self.print_label_niimprint(label, copies, use_usb=True, com_port=self.usb_port)
        
        # Ścieżka 2: BLE przez bleak (dla Niimbot B1 i innych BLE)
        if BLEAK_AVAILABLE and self.device_address:
            # Połącz przez bleak jeśli nie połączony
            if not self.connected:
                print(f"[PUSH] Łączenie BLE przez bleak ({self.device_address})...")
                connected = await self.connect(self.device_address)
                if not connected:
                    print("[ERR] Nie udało się połączyć przez BLE")
                    return False
            print("[PUSH] Drukuję przez BLE (bleak)")
            return await self._print_label_ble(label, copies)
        
        # Ścieżka 3: niimprint USB auto-detect
        if NIIMPRINT_AVAILABLE and SerialTransport:
            print("[PUSH] Próbuję niimprint USB (auto-detect COM)")
            return await self.print_label_niimprint(label, copies, use_usb=True)
        
        # Brak opcji
        print("[ERR] Drukarka nie jest połączona")
        print("   [LIGH] Wskazówki:")
        print("   - Upewnij się że Niimbot jest włączony i w zasięgu BT")
        print("   - Przejdź do Magazyn → Drukarka → Skanuj")
        if not BLEAK_AVAILABLE:
            print("   - Zainstaluj bleak: pip install bleak")
        return False
            
        if not IMAGING_AVAILABLE:
            print("[ERR] Brak bibliotek do generowania obrazu")
            return False
        
        print("[ERR] Brak możliwości drukowania - sprawdź połączenie")
        return False
    
    async def _print_label_ble(self, label: ProductLabel, copies: int = 1) -> bool:
        """
        Drukuje etykietę przez BLE (bleak) - fallback gdy brak niimprint.
        """
        print(f"[PRIN] Drukowanie etykiety (BLE): {label.nazwa[:30]}...")
        
        try:
            # 1. Generuj obraz
            img = self._generate_label_image(label)
            width_px, height_px = img.size

            print(f"  [STRA] Rozmiar: {width_px}x{height_px} px")

            # 2. Konfiguruj drukarkę
            print("  [SETT] Konfiguruję drukarkę...")
            await self._send_command(NiimbotCommand.SET_LABEL_TYPE, bytes([self.config.label_type]))
            await self._send_command(NiimbotCommand.SET_LABEL_DENSITY, bytes([self.config.density]))

            # Ustaw wymiary
            dim_data = struct.pack('>HH', width_px, height_px)
            await self._send_command(NiimbotCommand.SET_DIMENSION, dim_data)
            await asyncio.sleep(0.05)
            
            # 3. Drukuj kopie
            for copy in range(copies):
                if copies > 1:
                    print(f"  [DESC] Kopia {copy + 1}/{copies}")
                    
                # Start druku
                await self._send_command(NiimbotCommand.START_PRINT, bytes([1]))
                await self._send_command(NiimbotCommand.START_PAGE, bytes([1]))

                # Wyślij dane obrazu
                print_data = self._image_to_print_data(img)
                total_chunks = (len(print_data) + 199) // 200

                print(f"  [UPLO] Wysyłam {len(print_data)} bajtów ({total_chunks} pakietów)...")

                # Podziel na pakiety (max 200 bajtów na pakiet)
                chunk_size = 200
                chunk_num = 0
                for i in range(0, len(print_data), chunk_size):
                    chunk = print_data[i:i+chunk_size]
                    await self._send_command(NiimbotCommand.IMAGE_DATA, chunk, wait_response=False)
                    await asyncio.sleep(0.005)  # Minimalna pauza
                    chunk_num += 1

                # Krótka pauza na przetworzenie
                await asyncio.sleep(0.2)

                # Zakończ stronę i druk
                await self._send_command(NiimbotCommand.END_PAGE, bytes([1]))
                await asyncio.sleep(0.1)
                await self._send_command(NiimbotCommand.END_PRINT, bytes([1]))

                # Krótka pauza na druk mechaniczny
                await asyncio.sleep(0.5)
                
            print(f"[OK] Wydrukowano {copies} etykiet(ę)")
            return True
            
        except Exception as e:
            print(f"[ERR] Błąd drukowania: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def print_label_niimprint(self, label: ProductLabel, copies: int = 1, use_usb: bool = None, com_port: str = None) -> bool:
        """
        Drukuje etykietę używając biblioteki niimprint.
        
        Args:
            label: Dane etykiety ProductLabel
            copies: Liczba kopii
            use_usb: True = USB, False = Bluetooth, None = auto (BT first)
            com_port: Port COM dla USB (np. COM5)
            
        Returns:
            True jeśli sukces
        """
        if not NIIMPRINT_AVAILABLE:
            print("[ERR] Biblioteka niimprint nie jest zainstalowana")
            print("   Zainstaluj: py -3.11 -m pip install niimprint")
            return False
            
        if not IMAGING_AVAILABLE:
            print("[ERR] Brak bibliotek do generowania obrazu")
            return False
        
        # Auto-scan jeśli nie mamy adresu BT
        if not self.device_address and BluetoothTransport:
            print("[SEAR] Brak adresu drukarki - automatyczne skanowanie...")
            try:
                printers = await self.scan_printers(timeout=10)
                if printers:
                    self.device_address = printers[0].get('address')
                    self.device_name = printers[0].get('name', 'Niimbot')
                    print(f"  [OK] Znaleziono: {self.device_name} ({self.device_address})")
                    # Zapisz adres do bazy config żeby przetrwał restart
                    try:
                        from .database import get_db
                        with get_db() as conn:
                            conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                                        ('niimbot_address', self.device_address))
                            conn.commit()
                    except Exception as e:
                        print(f"  [WARN] Nie udało się zapisać adresu: {e}")
                else:
                    print("  [ERR] Nie znaleziono drukarek Niimbot w zasięgu BT")
            except Exception as e:
                print(f"  [WARN] Błąd skanowania: {e}")
            
        print(f"[PRIN] [niimprint] Drukowanie: {label.nazwa[:30]}...")
        
        transport = None
        try:
            # 1. Generuj obraz
            img = self._generate_label_image(label)
            original_size = img.size
            print(f"  [STRA] Obraz oryginalny: {original_size[0]}x{original_size[1]} px, mode={img.mode}")
            
            # 2. Obraz jest już portrait 240×384 — bez rotacji
            print(f"  [STRA] Portrait {img.size[0]}x{img.size[1]} px")
            
            # 3. Konwertuj do grayscale (niimprint wymaga L lub 1)
            if img.mode == '1':
                img = img.convert('L')
            elif img.mode != 'L':
                img = img.convert('L')
            
            print(f"  [PALE] Format: {img.mode}, rozmiar: {img.size}")
            
            # 4. Połącz - auto-detect: Bluetooth preferred, USB fallback
            # Określ tryb połączenia
            if use_usb is None:
                # Auto: preferuj Bluetooth jeśli mamy adres, fallback USB
                if BluetoothTransport and self.device_address:
                    use_usb = False
                elif SerialTransport and (com_port or self.usb_port):
                    use_usb = True
                else:
                    use_usb = False  # spróbuj BT mimo wszystko
            
            if not use_usb and BluetoothTransport and self.device_address:
                print(f"  [LINK] Łączenie przez Bluetooth ({self.device_address})...")
                transport = BluetoothTransport(self.device_address)
            elif use_usb and SerialTransport:
                port = com_port or self.usb_port or "COM5"
                print(f"  [LINK] Łączenie przez USB ({port})...")
                transport = SerialTransport(port)
            elif BluetoothTransport and self.device_address:
                # Fallback na BT jeśli USB nie działa
                print(f"  [LINK] Fallback: Bluetooth ({self.device_address})...")
                transport = BluetoothTransport(self.device_address)
            else:
                print("[ERR] Brak dostępnego transportu (USB/Bluetooth)")
                if not self.device_address:
                    print("   [LIGH] Najpierw sparuj drukarkę: Skanuj → Połącz w ustawieniach drukarki")
                return False
                
            printer = PrinterClient(transport)
            
            # 5. Drukuj kopie
            for i in range(copies):
                if copies > 1:
                    print(f"  [DESC] Kopia {i+1}/{copies}")
                    
                print("  [UPLO] Wysyłam do drukarki...")
                
                density = min(max(self.config.density, 1), 5)
                printer.print_image(img, density=density)
                
                print(f"  [OK] Wysłano kopię {i+1}")
                
                if i < copies - 1:
                    await asyncio.sleep(2)
                    
            print(f"[OK] Wydrukowano {copies} etykiet(ę) przez niimprint")
            return True
            
        except Exception as e:
            print(f"[ERR] Błąd niimprint: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            if transport:
                try:
                    print("  [POWE] Zamykam połączenie...")
                    transport.close()
                except:
                    pass
    
    def generate_label_preview(self, label: ProductLabel) -> str:
        """
        Generuje podgląd etykiety jako base64 PNG.
        
        Returns:
            String base64 do osadzenia w HTML: data:image/png;base64,...
        """
        if not IMAGING_AVAILABLE:
            return ""
            
        try:
            img = self._generate_label_image(label)
            # Konwertuj do RGB dla lepszego podglądu
            img_rgb = Image.new('RGB', img.size, 'white')
            img_rgb.paste(img.convert('L'))
            
            # Podgląd w oryginalnym rozmiarze (576px szerokości)
            # Skalowanie nie jest potrzebne - obraz jest już duży
            
            # Konwertuj do base64
            buffer = io.BytesIO()
            img_rgb.save(buffer, format='PNG')
            b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            return f"data:image/png;base64,{b64}"
            
        except Exception as e:
            print(f"[ERR] Błąd generowania podglądu: {e}")
            return ""
    
    # ============================================================
    # SPRAWDZANIE STATUSU
    # ============================================================
    
    async def get_status(self) -> dict:
        """Pobiera status drukarki"""
        status = {
            "connected": self.connected,
            "device": self.device_name,
            "address": self.device_address,
            "ready": False
        }
        
        if self.connected:
            try:
                response = await self._send_command(NiimbotCommand.GET_STATUS)
                if response:
                    status["ready"] = True
                    # Parsuj dodatkowe info ze statusu
            except:
                pass
                
        return status
    
    def is_available(self) -> bool:
        """Sprawdza czy biblioteki są dostępne"""
        return BLEAK_AVAILABLE and IMAGING_AVAILABLE


# ============================================================
# FUNKCJE POMOCNICZE (SYNC) - dla integracji z Flask
# ============================================================

def get_printer_manager() -> PrinterManager:
    """Singleton PrinterManager"""
    if not hasattr(get_printer_manager, '_instance'):
        get_printer_manager._instance = PrinterManager()
        # Wczytaj zapisany adres BT z bazy
        try:
            from .database import get_db
            conn = get_db()
            row = conn.execute("SELECT value FROM config WHERE key = 'niimbot_address'").fetchone()
            if row and row['value']:
                get_printer_manager._instance.device_address = row['value']
                print(f"[SMAR] Niimbot adres BT z config: {row['value']}")
        except Exception:
            pass
    return get_printer_manager._instance


def print_product_label_sync(
    nazwa: str,
    qr_data: str,
    lokalizacja: str = "",
    ean: str = "",
    copies: int = 1
) -> dict:
    """
    Synchroniczna funkcja do drukowania (wrapper dla Flask).
    Używa dedykowanego BLE event loop.
    
    Returns:
        {"success": bool, "message": str}
    """
    pm = get_printer_manager()
    
    if not pm.is_available():
        return {
            "success": False,
            "message": "Brak wymaganych bibliotek. Zainstaluj: pip install bleak qrcode pillow"
        }
    
    label = ProductLabel(
        nazwa=nazwa,
        qr_data=qr_data,
        lokalizacja=lokalizacja,
        ean=ean
    )
    
    async def _print():
        if not pm.connected and pm.device_address:
            connected = await pm.connect(pm.device_address)
            if not connected:
                return {"success": False, "message": "Nie można połączyć z drukarką"}
        elif not pm.device_address:
            return {"success": False, "message": "Brak adresu drukarki - najpierw skanuj"}
                
        success = await pm.print_label(label, copies)
        return {
            "success": success,
            "message": "Wydrukowano!" if success else "Błąd drukowania"
        }
    
    try:
        result = run_ble_async(_print(), timeout=45)
        return result
    except Exception as e:
        return {"success": False, "message": str(e)}


def generate_label_preview_sync(
    nazwa: str,
    qr_data: str,
    lokalizacja: str = "",
    ean: str = "",
    ilosc: int = 1,
    dostawca: str = "",
    data_zakupu: str = "",
    paleta: str = "",
    koszt_szt: float = 0,
    cena_allegro: float = 0,
    kod_magazynowy: str = "",
    stan_przyjecia: str = ""
) -> str:
    """
    Generuje podgląd etykiety (sync).

    Returns:
        Base64 string obrazu lub pusty string
    """
    pm = get_printer_manager()

    if not pm.is_available():
        return ""

    label = ProductLabel(
        nazwa=nazwa,
        qr_data=qr_data,
        lokalizacja=lokalizacja,
        ean=ean,
        ilosc=ilosc,
        dostawca=dostawca,
        data_zakupu=data_zakupu,
        paleta=paleta,
        koszt_szt=koszt_szt,
        cena_allegro=cena_allegro,
        kod_magazynowy=kod_magazynowy,
        stan_przyjecia=stan_przyjecia
    )

    return pm.generate_label_preview(label)


def scan_printers_sync() -> list:
    """Skanuje drukarki (sync) - używa dedykowanego BLE event loop"""
    pm = get_printer_manager()

    if not BLEAK_AVAILABLE:
        return [{"error": "Brak biblioteki bleak. Zainstaluj: pip install bleak"}]

    try:
        printers = run_ble_async(pm.scan_printers(), timeout=20)
        return printers
    except OSError as e:
        err_str = str(e).lower()
        if 'bluetooth' in err_str or 'radio' in err_str or 'winrt' in err_str:
            return [{"error": "Adapter Bluetooth niedostępny. Sprawdź czy Bluetooth jest włączony w ustawieniach Windows."}]
        return [{"error": f"Błąd systemu: {e}"}]
    except TimeoutError:
        return [{"error": "Timeout skanowania - spróbuj ponownie"}]
    except Exception as e:
        err_str = str(e)
        if 'WinError' in err_str or 'Access' in err_str:
            return [{"error": f"Brak dostępu do Bluetooth: {e}"}]
        return [{"error": f"Błąd skanowania: {e}"}]


def print_niimbot_ble_sync(
    nazwa: str,
    qr_data: str,
    lokalizacja: str = "",
    ean: str = "",
    bt_address: str = "",
    copies: int = 1,
    ilosc: int = 1,
    dostawca: str = "",
    data_zakupu: str = "",
    paleta: str = "",
    koszt_szt: float = 0,
    cena_allegro: float = 0,
    kod_magazynowy: str = ""
) -> dict:
    """
    Drukuje etykietę na Niimbot przez BLE używając niimprint + BleakTransport.

    Strategia:
    - BleakTransport daje nam BLE (bleak) jako transport
    - niimprint.PrinterClient obsługuje protokół Niimbot (prawidłowe pakiety/komendy)
    - Razem = poprawna komunikacja z Niimbot B1 przez BLE
    """
    if not BLEAK_AVAILABLE:
        return {"success": False, "message": "Brak biblioteki bleak. Zainstaluj: pip install bleak"}

    if not NIIMPRINT_AVAILABLE:
        return {"success": False, "message": "Brak biblioteki niimprint. Zainstaluj: py -3.11 -m pip install niimprint"}

    if not IMAGING_AVAILABLE:
        return {"success": False, "message": "Brak bibliotek obrazu (pillow/qrcode)"}

    if not bt_address:
        return {"success": False, "message": "Brak adresu BLE. Przejdź do Drukarka → Skanuj."}

    pm = get_printer_manager()

    label = ProductLabel(
        nazwa=nazwa,
        qr_data=qr_data,
        lokalizacja=lokalizacja,
        ean=ean,
        ilosc=ilosc,
        dostawca=dostawca,
        data_zakupu=data_zakupu,
        paleta=paleta,
        koszt_szt=koszt_szt,
        cena_allegro=cena_allegro,
        kod_magazynowy=kod_magazynowy
    )

    transport = None
    try:
        print(f"[PRIN] [BLE+niimprint] Drukowanie: {nazwa[:30]}...")
        print(f"   Adres BLE: {bt_address}, Kopie: {copies}")

        # 1. Generuj obraz etykiety
        img = pm._generate_label_image(label)
        print(f"   [STRA] Obraz: {img.size[0]}x{img.size[1]} px, mode={img.mode}")

        # 2. Konwertuj do grayscale (niimprint wymaga L)
        if img.mode != 'L':
            img = img.convert('L')

        # 3. Zapisz debug obraz
        try:
            from pathlib import Path
            debug_path = Path(__file__).parent.parent / 'debug_label.png'
            img.save(str(debug_path))
        except:
            pass

        # 4. Połącz przez BleakTransport (BLE via bleak)
        print(f"   [LINK] Łączenie BLE z {bt_address}...")
        transport = BleakTransport(bt_address)
        printer = PrinterClient(transport)
        print(f"   [OK] Transport BLE aktywny")

        import struct as _struct
        import time as _time
        density = min(max(pm.config.density, 1), 5)

        w_px, h_px = img.size
        print(f"   [STRA] Image: {w_px}x{h_px}, density={density}", flush=True)

        for i in range(copies):
            if copies > 1:
                print(f"   [DESC] Kopia {i + 1}/{copies}")

            # === PROTOKÓŁ B1 (ręczny — niimprint natywny nie działa z B1) ===
            # 1. Density + label type
            printer.set_label_density(density)
            printer.set_label_type(1)

            # 2. PrintStart — B1 wymaga 7 bajtów
            start_data = _struct.pack(">HBBBBB", 1, 0, 0, 0, 0, 0)
            printer._transceive(0x01, start_data)
            print(f"   [BUIL] PrintStart OK", flush=True)

            # 3. StartPagePrint
            printer.start_page_print()

            # 4. SetPageSize — B1: 6 bajtów (h, w, copies)
            page_data = _struct.pack(">HHH", h_px, w_px, 1)
            printer._transceive(0x13, page_data)
            print(f"   [BUIL] SetPageSize h={h_px} w={w_px} OK", flush=True)

            # 5. SetQuantity
            printer.set_quantity(1)

            # 6. Koduj obraz przez niimprint
            all_packets = list(printer._encode_image(img))
            total_lines = len(all_packets)
            print(f"   [INVE] {total_lines} linii do wysłania", flush=True)

            # 7. Wyślij linia po linii z pauzami co BATCH linii
            #    Drukarka B1 ma mały bufor — bez pauz ucina wydruk
            BATCH = 10
            BATCH_PAUSE = 0.3  # 300ms co 10 linii

            for idx, pkt in enumerate(all_packets):
                printer._send(pkt)

                # Pauza co BATCH linii — drukarka przetwarza dane
                if (idx + 1) % BATCH == 0:
                    _time.sleep(BATCH_PAUSE)

                if (idx + 1) % 50 == 0 or idx == total_lines - 1:
                    print(f"   [BAR_] Sent {idx+1}/{total_lines}", flush=True)

            # 8. Zakończ stronę i druk
            _time.sleep(0.5)
            try:
                printer.end_page_print()
            except:
                pass
            _time.sleep(0.3)
            try:
                printer.end_print()
            except:
                pass

            # 9. Czekaj na zakończenie fizycznego druku
            for _ in range(30):
                try:
                    status = printer.get_print_status()
                    if status and status.get("progress1", 0) >= 100:
                        break
                except:
                    pass
                _time.sleep(0.3)

            print(f"   [OK] Kopia {i + 1} gotowa", flush=True)
            if i < copies - 1:
                _time.sleep(2)

        print(f"[OK] Wydrukowano {copies} etykiet(ę) przez BLE")
        return {"success": True, "message": f"Wydrukowano {copies} etykiet przez BLE"}

    except TimeoutError:
        print(f"[ERR] Timeout BLE")
        return {"success": False, "message": "Timeout — drukarka nie odpowiada. Sprawdź czy jest włączona i w zasięgu."}
    except ValueError as e:
        print(f"[ERR] Błąd protokołu Niimbot: {e}")
        return {"success": False, "message": f"Błąd protokołu: {e}. Spróbuj wyłączyć i włączyć drukarkę."}
    except Exception as e:
        msg = str(e)
        print(f"[ERR] BLE print error: {e}")
        import traceback
        traceback.print_exc()
        if "not connected" in msg.lower() or "disconnect" in msg.lower():
            msg = "Drukarka rozłączona. Przejdź do Drukarka → Skanuj → Połącz."
        elif "nie udało się połączyć" in msg.lower() or "failed to connect" in msg.lower():
            msg = "Nie udało się połączyć. Sprawdź czy drukarka jest włączona i w zasięgu."
        elif "nie znaleziono" in msg.lower():
            msg = "Nie znaleziono charakterystyki BLE. Drukarka może nie być kompatybilna."
        return {"success": False, "message": msg}
    finally:
        if transport:
            try:
                transport.close()
            except:
                pass


# ============================================================
# NIIMBOT USB - Drukowanie przez SerialTransport
# ============================================================

def print_niimbot_usb_sync(
    nazwa: str,
    qr_data: str,
    lokalizacja: str = "",
    ean: str = "",
    com_port: str = "COM5",
    copies: int = 1,
    ilosc: int = 1,
    dostawca: str = "",
    data_zakupu: str = "",
    paleta: str = "",
    koszt_szt: float = 0,
    cena_allegro: float = 0,
    kod_magazynowy: str = ""
) -> dict:
    """
    Drukuje etykietę na Niimbot przez USB (COM port).
    
    Args:
        nazwa: Nazwa produktu
        qr_data: Dane do QR code
        lokalizacja: Lokalizacja magazynowa (regał)
        ean: Kod EAN
        com_port: Port COM (np. COM5)
        copies: Liczba kopii
        ilosc: Ilość sztuk produktu
        dostawca: Dostawca (Jobalots, Warrington, etc.)
        data_zakupu: Data zakupu palety
        
    Returns:
        {"success": bool, "message": str}
    """
    if not NIIMPRINT_AVAILABLE:
        return {"success": False, "message": "Brak biblioteki niimprint. Zainstaluj: py -3.11 -m pip install niimprint"}
    
    if not SerialTransport:
        return {"success": False, "message": "SerialTransport niedostępny - zaktualizuj niimprint"}
    
    if not IMAGING_AVAILABLE:
        return {"success": False, "message": "Brak bibliotek obrazu (pillow/qrcode)"}
    
    pm = get_printer_manager()
    
    label = ProductLabel(
        nazwa=nazwa,
        qr_data=qr_data,
        lokalizacja=lokalizacja,
        ean=ean,
        ilosc=ilosc,
        dostawca=dostawca,
        data_zakupu=data_zakupu,
        paleta=paleta,
        koszt_szt=koszt_szt,
        cena_allegro=cena_allegro,
        kod_magazynowy=kod_magazynowy
    )

    transport = None
    try:
        print(f"[PRIN] [USB] Drukowanie: {nazwa[:30]}...")
        print(f"   Port: {com_port}, Kopie: {copies}")
        
        # Generuj obraz
        img = pm._generate_label_image(label)
        print(f"   Obraz: {img.size[0]}x{img.size[1]} px")
        
        # Obraz jest już portrait 240×384 — bez rotacji

        # Konwertuj do grayscale
        if img.mode != 'L':
            img = img.convert('L')
        
        # Połącz przez USB
        print(f"   Łączenie z {com_port}...")
        transport = SerialTransport(com_port)
        printer = PrinterClient(transport)
        
        # Drukuj kopie
        for i in range(copies):
            if copies > 1:
                print(f"   Kopia {i+1}/{copies}...")
            printer.print_image(img, density=3)
        
        # Zamknij połączenie
        transport.close()
        transport = None
        
        print(f"[OK] Wydrukowano {copies} etykiet")
        return {"success": True, "message": f"Wydrukowano {copies} etykiet"}
        
    except Exception as e:
        print(f"[ERR] Błąd USB: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": str(e)}
    finally:
        if transport:
            try:
                transport.close()
            except:
                pass


# ============================================================
# NIIMBOT BLUETOOTH - Drukowanie przez Bluetooth RFCOMM
# ============================================================

def print_niimbot_bt_sync(
    nazwa: str,
    qr_data: str,
    lokalizacja: str = "",
    ean: str = "",
    bt_address: str = "",
    copies: int = 1,
    ilosc: int = 1,
    dostawca: str = "",
    data_zakupu: str = "",
    paleta: str = "",
    koszt_szt: float = 0,
    cena_allegro: float = 0,
    kod_magazynowy: str = ""
) -> dict:
    """
    Drukuje etykietę na Niimbot przez Bluetooth (RFCOMM).

    Args:
        bt_address: Adres MAC Bluetooth (np. "AA:BB:CC:DD:EE:FF")
    """
    if not NIIMPRINT_AVAILABLE:
        return {"success": False, "message": "Brak biblioteki niimprint"}

    if not BluetoothTransport:
        return {"success": False, "message": "BluetoothTransport niedostępny"}

    if not IMAGING_AVAILABLE:
        return {"success": False, "message": "Brak bibliotek obrazu (pillow/qrcode)"}

    if not bt_address:
        return {"success": False, "message": "Brak adresu Bluetooth. Ustaw w Ustawienia drukarki."}

    pm = get_printer_manager()

    label = ProductLabel(
        nazwa=nazwa,
        qr_data=qr_data,
        lokalizacja=lokalizacja,
        ean=ean,
        ilosc=ilosc,
        dostawca=dostawca,
        data_zakupu=data_zakupu,
        paleta=paleta,
        koszt_szt=koszt_szt,
        cena_allegro=cena_allegro,
        kod_magazynowy=kod_magazynowy
    )

    transport = None
    try:
        print(f"[PRIN] [BT] Drukowanie: {nazwa[:30]}...")
        print(f"   Adres BT: {bt_address}, Kopie: {copies}")

        # Generuj obraz
        img = pm._generate_label_image(label)
        print(f"   Obraz: {img.size[0]}x{img.size[1]} px")

        # Obraz jest już portrait 240×384 — bez rotacji

        # Konwertuj do grayscale
        if img.mode != 'L':
            img = img.convert('L')

        # Połącz przez Bluetooth RFCOMM
        print(f"   Łączenie BT z {bt_address}...")
        transport = BluetoothTransport(bt_address)
        printer = PrinterClient(transport)

        for i in range(copies):
            if copies > 1:
                print(f"   Kopia {i+1}/{copies}...")
            printer.print_image(img, density=3)

        transport.close()
        transport = None

        print(f"[OK] [BT] Wydrukowano {copies} etykiet")
        return {"success": True, "message": f"Wydrukowano {copies} etykiet przez Bluetooth"}

    except Exception as e:
        print(f"[ERR] Błąd BT: {e}")
        import traceback
        traceback.print_exc()
        msg = str(e)
        if "No route to host" in msg or "Connection refused" in msg:
            msg = "Nie mogę połączyć — sprawdź czy Niimbot jest włączony i sparowany"
        elif "AF_BLUETOOTH" in msg:
            msg = "Bluetooth niedostępny na tym komputerze"
        return {"success": False, "message": msg}
    finally:
        if transport:
            try:
                transport.close()
            except:
                pass


def scan_niimbot_bt() -> list:
    """Skanuje urządzenia Bluetooth szukając Niimbot."""
    import subprocess
    results = []
    try:
        # Windows: użyj PowerShell do listowania sparowanych urządzeń BT
        out = subprocess.run(
            ['powershell', '-Command',
             'Get-PnpDevice -Class Bluetooth | Where-Object {$_.FriendlyName -like "*Niimbot*" -or $_.FriendlyName -like "*B1*" -or $_.FriendlyName -like "*B21*" -or $_.FriendlyName -like "*D11*" -or $_.FriendlyName -like "*D110*"} | Select-Object FriendlyName, InstanceId'],
            capture_output=True, text=True, timeout=10
        )
        if out.stdout:
            for line in out.stdout.strip().split('\n'):
                line = line.strip()
                if line and not line.startswith('---') and not line.startswith('FriendlyName'):
                    results.append(line)
    except:
        pass
    return results


# ============================================================
# VRETTI 420B - Drukarka USB (drukarka systemowa)
# ============================================================

class VrettiPrinter:
    """
    Obsługa drukarki Vretti 420B przez drukowanie systemowe.
    
    Vretti 420B to drukarka termiczna USB która działa jako 
    normalna drukarka Windows/Linux. Drukujemy przez system.
    
    Użycie:
        vp = VrettiPrinter()
        printers = vp.list_system_printers()
        vp.print_label(ProductLabel(...), printer_name="Vretti 420B")
    """
    
    def __init__(self, label_width_mm: int = 100, label_height_mm: int = 60, dpi: int = 203):
        """
        Args:
            label_width_mm: Szerokość etykiety w mm (domyślnie 100mm dla Vretti)
            label_height_mm: Wysokość etykiety w mm
            dpi: Rozdzielczość drukarki
        """
        self.label_width_mm = label_width_mm
        self.label_height_mm = label_height_mm
        self.dpi = dpi
        self.default_printer = None
        
    def list_system_printers(self) -> List[dict]:
        """
        Listuje drukarki systemowe.
        
        Returns:
            Lista słowników z informacjami o drukarkach
        """
        printers = []
        
        # Windows
        try:
            import subprocess
            result = subprocess.run(
                ['wmic', 'printer', 'get', 'name,default'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')[1:]  # Pomiń nagłówek
                for line in lines:
                    line = line.strip()
                    if line:
                        # Format WMIC: "Default  Name" gdzie Default to TRUE/FALSE
                        # Przykład: "TRUE     4BARCODE 4B-2054L" lub "FALSE    HP LaserJet"
                        is_default = line.upper().startswith('TRUE')
                        # Usuń TRUE/FALSE z początku i weź resztę jako nazwę
                        if line.upper().startswith('TRUE'):
                            name = line[4:].strip()  # Usuń "TRUE" i whitespace
                        elif line.upper().startswith('FALSE'):
                            name = line[5:].strip()  # Usuń "FALSE" i whitespace
                        else:
                            name = line  # Fallback - cała linia
                        
                        if name:  # Tylko jeśli nazwa nie jest pusta
                            printers.append({
                                "name": name,
                                "type": "system",
                                "default": is_default,
                                "platform": "windows"
                            })
                            if is_default:
                                self.default_printer = name
        except Exception as e:
            pass
            
        # Linux
        if not printers:
            try:
                import subprocess
                result = subprocess.run(
                    ['lpstat', '-p'],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if 'printer' in line.lower():
                            parts = line.split()
                            if len(parts) >= 2:
                                name = parts[1]
                                printers.append({
                                    "name": name,
                                    "type": "system",
                                    "default": False,
                                    "platform": "linux"
                                })
            except Exception as e:
                pass
                
        # Filtruj dla Vretti
        vretti_printers = [p for p in printers if 'vretti' in p['name'].lower() or '420' in p['name']]
        
        if vretti_printers:
            return vretti_printers
        return printers
    
    def generate_label_image(self, label: ProductLabel) -> Any:
        """
        Generuje obraz etykiety dla Vretti (większy format).
        
        Vretti 420B drukuje większe etykiety wysyłkowe,
        więc layout jest inny niż dla Niimbot.
        """
        if not IMAGING_AVAILABLE:
            raise RuntimeError("Brak biblioteki Pillow")
            
        # Wymiary w pikselach
        width_px = int(self.label_width_mm * self.dpi / 25.4)
        height_px = int(self.label_height_mm * self.dpi / 25.4)
        
        # Białe tło (RGB dla lepszej jakości druku)
        img = Image.new('RGB', (width_px, height_px), color='white')
        draw = ImageDraw.Draw(img)
        
        # Czcionki - większe dla Vretti
        font_title = None
        font_normal = None
        font_small = None
        
        font_paths = [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        
        for font_path in font_paths:
            try:
                font_title = ImageFont.truetype(font_path, 36)   # Było 28 → 36
                font_normal = ImageFont.truetype(font_path, 26)  # Było 20 → 26
                font_small = ImageFont.truetype(font_path, 20)   # Było 16 → 20
                break
            except:
                continue
                
        if not font_title:
            font_title = ImageFont.load_default()
            font_normal = font_title
            font_small = font_title
        
        # Generuj QR code - z odpowiednim marginesem (quiet zone)
        # WAŻNE: border=4 to MINIMUM wg standardu ISO/IEC 18004
        qr = qrcode.QRCode(
            version=2,      # Wyższa wersja dla większej pojemności
            box_size=5,     # Większy moduł dla lepszego druku termicznego
            border=4,       # KRYTYCZNE: minimum 4 dla prawidłowego skanowania!
            error_correction=qrcode.constants.ERROR_CORRECT_M  # 15% korekcji błędów
        )
        qr.add_data(label.qr_data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        
        # Rozmiar QR - zwiększony do 40% dla lepszej czytelności
        qr_size = int(width_px * 0.40)
        qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
        
        # Pozycja QR (prawy)
        qr_x = width_px - qr_size - 20
        qr_y = 20
        img.paste(qr_img, (qr_x, qr_y))
        
        # Tekst po lewej
        margin = 20
        text_max_width = qr_x - 30
        y = margin
        
        # Nazwa produktu (mniej znaków bo większa czcionka)
        nazwa = label.nazwa[:28]  # Było 35 → 28
        if len(label.nazwa) > 28:
            nazwa = nazwa[:-2] + ".."
        draw.text((margin, y), nazwa, font=font_title, fill='black')
        y += 50  # Było 40 → 50
        
        # Linia oddzielająca
        draw.line([(margin, y), (text_max_width, y)], fill='gray', width=2)  # Grubsza linia
        y += 20  # Było 15 → 20
        
        # Data przyjęcia
        draw.text((margin, y), f"Data: {label.data_przyjecia}", font=font_normal, fill='black')
        y += 38  # Było 30 → 38
        
        # Lokalizacja
        if label.lokalizacja:
            draw.text((margin, y), f"Lokalizacja: {label.lokalizacja}", font=font_normal, fill='black')
            y += 38  # Było 30 → 38
            
        # EAN
        if label.ean:
            draw.text((margin, y), f"EAN: {label.ean}", font=font_normal, fill='black')
            y += 38  # Było 30 → 38
        
        # Ramka wokół etykiety
        draw.rectangle([(2, 2), (width_px-3, height_px-3)], outline='black', width=3)  # Grubsza ramka
        
        return img
    
    def generate_label_preview(self, label: ProductLabel) -> str:
        """Generuje podgląd jako base64"""
        try:
            img = self.generate_label_image(label)
            
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            return f"data:image/png;base64,{b64}"
        except Exception as e:
            print(f"[ERR] Błąd podglądu Vretti: {e}")
            return ""
    
    def print_label(self, label: ProductLabel, printer_name: str = None, copies: int = 1) -> dict:
        """
        Drukuje etykietę na drukarce Vretti.
        
        Args:
            label: Dane etykiety
            printer_name: Nazwa drukarki (domyślnie pierwsza Vretti)
            copies: Liczba kopii
            
        Returns:
            {"success": bool, "message": str}
        """
        import subprocess
        import tempfile
        import os
        
        if not IMAGING_AVAILABLE:
            return {"success": False, "message": "Brak biblioteki Pillow"}
        
        # Znajdź drukarkę
        if not printer_name:
            printers = self.list_system_printers()
            vretti = [p for p in printers if 'vretti' in p['name'].lower()]
            if vretti:
                printer_name = vretti[0]['name']
            elif printers:
                printer_name = printers[0]['name']
            else:
                return {"success": False, "message": "Nie znaleziono drukarki"}
        
        try:
            # Generuj obraz
            img = self.generate_label_image(label)
            
            # Zapisz do pliku tymczasowego
            tmp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            img.save(tmp_file.name, 'PNG')
            tmp_file.close()
            
            success = False
            
            # Windows - użyj mspaint do drukowania
            if os.name == 'nt':
                for _ in range(copies):
                    # Metoda 1: przez mspaint (silent)
                    try:
                        result = subprocess.run(
                            ['mspaint', '/pt', tmp_file.name, printer_name],
                            capture_output=True, timeout=30
                        )
                        success = True
                    except:
                        # Metoda 2: przez PowerShell
                        try:
                            ps_cmd = f'Start-Process -FilePath "{tmp_file.name}" -Verb Print'
                            result = subprocess.run(
                                ['powershell', '-Command', ps_cmd],
                                capture_output=True, timeout=30
                            )
                            success = True
                        except:
                            pass
            else:
                # Linux - użyj lp
                for _ in range(copies):
                    try:
                        result = subprocess.run(
                            ['lp', '-d', printer_name, tmp_file.name],
                            capture_output=True, timeout=30
                        )
                        if result.returncode == 0:
                            success = True
                    except:
                        pass
            
            # Usuń plik tymczasowy
            try:
                os.unlink(tmp_file.name)
            except:
                pass
                
            if success:
                return {"success": True, "message": f"Wydrukowano {copies} etykiet na {printer_name}"}
            else:
                return {"success": False, "message": "Błąd drukowania - sprawdź drukarkę"}
                
        except Exception as e:
            return {"success": False, "message": f"Błąd: {str(e)}"}


def get_vretti_printer() -> VrettiPrinter:
    """Singleton VrettiPrinter"""
    if not hasattr(get_vretti_printer, '_instance'):
        get_vretti_printer._instance = VrettiPrinter()
    return get_vretti_printer._instance


def print_vretti_label_sync(
    nazwa: str,
    qr_data: str,
    lokalizacja: str = "",
    ean: str = "",
    printer_name: str = None,
    copies: int = 1
) -> dict:
    """Sync wrapper do drukowania na Vretti"""
    vp = get_vretti_printer()
    
    if not IMAGING_AVAILABLE:
        return {"success": False, "message": "Brak biblioteki Pillow"}
    
    label = ProductLabel(
        nazwa=nazwa,
        qr_data=qr_data,
        lokalizacja=lokalizacja,
        ean=ean
    )
    
    return vp.print_label(label, printer_name, copies)


def generate_vretti_preview_sync(
    nazwa: str,
    qr_data: str,
    lokalizacja: str = "",
    ean: str = ""
) -> str:
    """Generuje podgląd etykiety Vretti"""
    vp = get_vretti_printer()
    
    if not IMAGING_AVAILABLE:
        return ""
    
    label = ProductLabel(
        nazwa=nazwa,
        qr_data=qr_data,
        lokalizacja=lokalizacja,
        ean=ean
    )
    
    return vp.generate_label_preview(label)


def list_system_printers_sync() -> list:
    """Listuje drukarki systemowe"""
    vp = get_vretti_printer()
    return vp.list_system_printers()



    # ============================================================
    # OBSŁUGA USB (SERIAL PORT)
    # ============================================================
    
    def find_usb_printer(self):
        """
        Automatyczne wykrywanie drukarki Niimbot B1 na USB
        
        Returns:
            str: Port COM (np. 'COM5') lub None jeśli nie znaleziono
        """
        if not SERIAL_AVAILABLE:
            print("[ERR] Biblioteka pyserial nie jest zainstalowana")
            print("   Zainstaluj: pip install pyserial --break-system-packages")
            return None
        
        print("[SEAR] Szukam drukarki Niimbot B1 na USB...")
        
        try:
            ports = serial.tools.list_ports.comports()
            
            for port in ports:
                # Niimbot B1 ma VID 0x3513 (13587 w decimal)
                if port.vid == 0x3513:
                    print(f"[OK] Znaleziono drukarkę Niimbot B1")
                    print(f"   Port: {port.device}")
                    print(f"   Opis: {port.description}")
                    print(f"   VID:PID = {hex(port.vid)}:{hex(port.pid)}")
                    return port.device
            
            print("[WARN]  Nie znaleziono drukarki Niimbot na USB")
            print("[LIGH] Sprawdź:")
            print("   1. Czy drukarka jest podłączona przez USB-C")
            print("   2. Czy drukarka jest włączona")
            print("   3. Czy Windows wykryło urządzenie (Menedżer urządzeń)")
            return None
            
        except Exception as e:
            print(f"[ERR] Błąd skanowania USB: {e}")
            return None
    
    def connect_usb(self, port='COM5', baudrate=115200):
        """
        Łączy się z drukarką przez port USB (COM)
        
        Args:
            port: Port COM (domyślnie COM5)
            baudrate: Prędkość transmisji (domyślnie 115200)
        
        Returns:
            bool: True jeśli połączono, False w przeciwnym wypadku
        """
        if not SERIAL_AVAILABLE:
            print("[ERR] Biblioteka pyserial nie jest zainstalowana")
            return False
        
        try:
            print(f"[POWE] Łączę się z drukarką na {port}...")
            
            # Stwórz połączenie serial
            self.usb_connection = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=2
            )
            
            import time
            time.sleep(0.5)  # Daj czas na inicjalizację
            
            print(f"[OK] Połączono przez USB: {port}")
            self.connection_type = 'usb'
            self.usb_port = port
            self.connected = True
            return True
            
        except serial.SerialException as e:
            print(f"[ERR] Błąd połączenia USB: {e}")
            self.usb_connection = None
            return False
        except Exception as e:
            print(f"[ERR] Błąd: {e}")
            self.usb_connection = None
            return False
    
    def disconnect_usb(self):
        """
        Rozłącza połączenie USB
        """
        try:
            if self.usb_connection and self.usb_connection.is_open:
                self.usb_connection.close()
                print("[OK] Rozłączono USB")
                self.connected = False
                self.connection_type = 'bluetooth'
                return True
        except Exception as e:
            print(f"[WARN]  Błąd rozłączania USB: {e}")
        
        return False
    
    def send_usb_command(self, command, data=None):
        """
        Wysyła komendę do drukarki przez USB
        
        Protokół Niimbot:
        [0x55] [0x55] [CMD] [LEN] [DATA...] [CHECKSUM]
        
        Args:
            command: Kod komendy (1 bajt)
            data: Opcjonalne dane (bytes lub None)
        
        Returns:
            bytes: Odpowiedź od drukarki lub None
        """
        if not self.usb_connection or not self.usb_connection.is_open:
            print("[ERR] Brak połączenia USB")
            return None
        
        try:
            # Buduj pakiet
            packet = bytearray([0x55, 0x55, command])
            
            if data:
                packet.append(len(data))
                packet.extend(data)
            else:
                packet.append(0x00)
            
            # Checksum (XOR wszystkich bajtów po nagłówku)
            checksum = 0
            for byte in packet[2:]:
                checksum ^= byte
            packet.append(checksum)
            
            # Wyślij
            self.usb_connection.write(packet)
            self.usb_connection.flush()
            
            # Czekaj na odpowiedź
            import time
            time.sleep(0.1)
            
            if self.usb_connection.in_waiting > 0:
                response = self.usb_connection.read(self.usb_connection.in_waiting)
                return response
            
            return None
            
        except Exception as e:
            print(f"[ERR] Błąd wysyłania komendy USB: {e}")
            return None
    
    async def print_label_usb(self, label, copies=1):
        """
        Drukuje etykietę przez USB - PEŁNA IMPLEMENTACJA
        
        Args:
            label: Obiekt ProductLabel
            copies: Liczba kopii (domyślnie 1)
        
        Returns:
            bool: True jeśli wydrukowano pomyślnie
        """
        print(f"\n[PRIN]  Drukowanie przez USB (kopii: {copies})...")
        
        try:
            # Sprawdź połączenie
            if not self.usb_connection:
                port = self.find_usb_printer()
                if not port:
                    print("[ERR] Nie znaleziono drukarki USB")
                    return False
                
                if not self.connect_usb(port):
                    print("[ERR] Nie udało się połączyć przez USB")
                    return False
            
            # Generuj obraz etykiety
            print("[PALE] Generuję etykietę...")
            img = self._generate_label_image(label)
            if not img:
                print("[ERR] Nie udało się wygenerować etykiety")
                return False
            
            width_px, height_px = img.size
            print(f"[OK] Etykieta wygenerowana: {width_px}x{height_px} px")
            
            # Drukuj dla każdej kopii
            for copy in range(copies):
                if copies > 1:
                    print(f"\n[DESC] Kopia {copy + 1}/{copies}")
                
                # 1. Konfiguracja drukarki
                print("[SETT]  Konfiguruję drukarkę...")
                self.send_usb_command(0x21, bytes([self.config.density]))
                import time
                time.sleep(0.1)
                self.send_usb_command(0x23, bytes([self.config.label_type]))
                time.sleep(0.1)
                
                # 2. Ustaw wymiary
                import struct
                dim_data = struct.pack('>HH', width_px, height_px)
                self.send_usb_command(0x13, dim_data)
                time.sleep(0.2)
                
                # 3. Start drukowania
                print("▶  Rozpoczynam druk...")
                self.send_usb_command(0x01, bytes([1]))
                time.sleep(0.1)
                self.send_usb_command(0x03, bytes([1]))
                time.sleep(0.1)
                
                # 4. Wyślij dane obrazu
                print_data = self._image_to_print_data(img)
                total_chunks = (len(print_data) + 199) // 200
                
                print(f"[UPLO] Wysyłam dane ({len(print_data)} bajtów, {total_chunks} pakietów)...")
                
                chunk_size = 200
                for i in range(0, len(print_data), chunk_size):
                    chunk = print_data[i:i+chunk_size]
                    self.send_usb_command(0x85, chunk)
                    time.sleep(0.05)
                    
                    if (i // chunk_size) % 10 == 0:
                        progress = int((i / len(print_data)) * 100)
                        print(f"  [BAR_] Progress: {progress}%")
                
                print("  [OK] 100% - dane wysłane")
                
                # 5. Zakończ drukowanie
                print("⏹  Kończę druk...")
                time.sleep(0.2)
                self.send_usb_command(0xE3, bytes([1]))
                time.sleep(0.1)
                self.send_usb_command(0x83, bytes([1]))
                time.sleep(0.3)
            
            print("\n[OK] WYDRUKOWANO!")
            print("[LIGH] Sprawdź drukarkę - etykieta powinna wyjechać!\n")
            return True
            
        except Exception as e:
            print(f"[ERR] Błąd drukowania USB: {e}")
            import traceback
            traceback.print_exc()
            return False

    
    def get_printer_info_usb(self):
        """
        Pobiera informacje o drukarce przez USB
        
        Returns:
            dict: Informacje o drukarce lub None
        """
        print("\n[ASSI] Pobieram informacje o drukarce (USB)...")
        
        try:
            # Komenda 0xC3 = Get Printer Info
            response = self.send_usb_command(0xC3)
            
            if response:
                print(f"[OK] Otrzymano odpowiedź ({len(response)} bajtów)")
                return {"raw_response": response.hex(), "port": self.usb_port}
            else:
                print("[WARN]  Brak odpowiedzi")
                return None
                
        except Exception as e:
            print(f"[ERR] Błąd: {e}")
            return None
    
    async def connect_auto(self, prefer_usb=True):
        """
        Automatyczne łączenie - najpierw USB, potem Bluetooth
        
        Args:
            prefer_usb: Jeśli True, preferuj USB nad Bluetooth
        
        Returns:
            bool: True jeśli połączono
        """
        print("\n[POWE] Automatyczne łączenie z drukarką...")
        
        if prefer_usb and SERIAL_AVAILABLE:
            # Najpierw spróbuj USB
            port = self.find_usb_printer()
            if port:
                if self.connect_usb(port):
                    print("[OK] Połączono przez USB")
                    return True
            
            # Jeśli USB nie działa, spróbuj Bluetooth
            print("\n[WARN]  USB niedostępny, próbuję Bluetooth...")
        
        # Fallback na Bluetooth
        if BLEAK_AVAILABLE:
            print("[SEAR] Skanowanie drukarek Bluetooth...")
            printers = await self.scan_printers(timeout=5.0)
            
            if printers and not printers[0].get('error'):
                address = printers[0].get('address')
                if address:
                    return await self.connect(address)
        
        print("[ERR] Nie udało się połączyć ani przez USB ani Bluetooth")
        return False


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":
    print("[PRIN] Test PrinterManager")
    print(f"  Bleak: {'[OK]' if BLEAK_AVAILABLE else '[ERR]'}")
    print(f"  Imaging: {'[OK]' if IMAGING_AVAILABLE else '[ERR]'}")
    print(f"  Niimprint: {'[OK]' if NIIMPRINT_AVAILABLE else '[ERR]'}")
    
    if not NIIMPRINT_AVAILABLE:
        status = get_niimprint_status()
        print(f"    Błąd: {status['error']}")
        print(f"    Instalacja: {status['install_cmd']}")
    
    if BLEAK_AVAILABLE and IMAGING_AVAILABLE:
        # Test generowania etykiety
        pm = PrinterManager()
        label = ProductLabel(
            nazwa="Ładowarka EV 22kW Type2",
            qr_data="https://allegro.pl/oferta/123456789",
            lokalizacja="A1-03",
            ean="5901234567890"
        )
        
        preview = pm.generate_label_preview(label)
        if preview:
            print(f"  [OK] Wygenerowano podgląd ({len(preview)} bajtów)")
        else:
            print("  [ERR] Nie udało się wygenerować podglądu")
