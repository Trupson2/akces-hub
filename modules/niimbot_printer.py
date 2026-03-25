"""
Niimbot B1 BLE Integration
Własna implementacja protokołu Niimbot dla pełnej kontroli
"""
import asyncio
from bleak import BleakClient, BleakScanner
from PIL import Image, ImageDraw, ImageFont
import io
from typing import Optional, Tuple
from dataclasses import dataclass

# UUID charakterystyki Niimbot B1
NIIMBOT_CHAR_UUID = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"

# Komendy Niimbot (reverse engineered)
CMD_GET_INFO = bytes([0x1b, 0x69])
CMD_GET_RFID = bytes([0x1b, 0x1a, 0x03])
CMD_START_PRINT = bytes([0x1b, 0x01])
CMD_END_PRINT = bytes([0x1b, 0x02])
CMD_START_PAGE = bytes([0x1b, 0x03])
CMD_END_PAGE = bytes([0x1b, 0x04])
CMD_SET_LABEL_TYPE = bytes([0x1b, 0x08])
CMD_SET_LABEL_DENSITY = bytes([0x1b, 0x09])


@dataclass
class NiimbotConfig:
    """Konfiguracja etykiety Niimbot"""
    width: int = 384  # 40mm @ 203 DPI
    height: int = 200
    density: int = 3  # 1-5, gdzie 3 = normalny
    label_type: int = 1  # Typ etykiety


class NiimbotPrinter:
    """Obsługa drukarki Niimbot B1 przez BLE"""
    
    def __init__(self, device_address: Optional[str] = None):
        """
        Args:
            device_address: Adres BT (np. "21:08:12:8A:83:92") lub None aby auto-wykryć
        """
        self.device_address = device_address
        self.client: Optional[BleakClient] = None
        self.config = NiimbotConfig()
        
    async def find_printer(self) -> Optional[str]:
        """Skanuje i znajduje drukarkę Niimbot"""
        print("[SEAR] Skanowanie drukarek Niimbot...")
        
        devices = await BleakScanner.discover(timeout=10.0)
        
        for device in devices:
            if device.name and any(kw in device.name.upper() for kw in ['NIIMBOT', 'B1', 'D11', 'B21']):
                print(f"[OK] Znaleziono: {device.name} ({device.address})")
                return device.address
        
        print("[ERR] Nie znaleziono drukarki Niimbot")
        return None
    
    async def connect(self) -> bool:
        """Łączy się z drukarką"""
        try:
            # Auto-wykryj jeśli nie podano adresu
            if not self.device_address:
                self.device_address = await self.find_printer()
                if not self.device_address:
                    return False
            
            print(f"[SATE] Łączenie z {self.device_address}...")
            self.client = BleakClient(self.device_address, timeout=30.0)
            await self.client.connect()
            
            if self.client.is_connected:
                print("[OK] Połączono z drukarką Niimbot!")
                return True
            else:
                print("[ERR] Nie udało się połączyć")
                return False
                
        except Exception as e:
            print(f"[ERR] Błąd połączenia: {e}")
            return False
    
    async def disconnect(self):
        """Rozłącza się z drukarką"""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            print("📴 Rozłączono")
    
    async def send_command(self, command: bytes) -> bool:
        """Wysyła komendę do drukarki z retry logic dla Windows BLE"""
        try:
            if not self.client or not self.client.is_connected:
                print("[ERR] Drukarka nie jest połączona")
                return False
            
            # RETRY LOGIC - Windows może anulować pierwsze próby
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await self.client.write_gatt_char(NIIMBOT_CHAR_UUID, command, response=False)
                    await asyncio.sleep(0.05)
                    return True
                except Exception as e:
                    error_msg = str(e)
                    if 'anulowana' in error_msg or 'cancelled' in error_msg.lower() or '2147023673' in error_msg:
                        if attempt < max_retries - 1:
                            print(f"[WARN] Windows anulował - retry {attempt + 1}/{max_retries}")
                            await asyncio.sleep(0.5)
                            continue
                        else:
                            print(f"[ERR] Windows anulował {max_retries}x")
                            return False
                    else:
                        print(f"[ERR] Błąd wysyłania komendy: {e}")
                        return False
            
            return False
            
        except Exception as e:
            print(f"[ERR] Błąd wysyłania komendy: {e}")
            return False
    
    async def print_image(self, image: Image.Image) -> bool:
        """
        Drukuje obraz na etykiecie
        
        Args:
            image: Obraz PIL (będzie skonwertowany do 1-bit B&W)
        """
        try:
            if not self.client or not self.client.is_connected:
                print("[ERR] Drukarka nie jest połączona")
                return False
            
            # Przygotuj obraz
            print("[IMAG] Przygotowywanie obrazu...")
            img = self._prepare_image(image)
            
            # Rozpocznij drukowanie
            print("[PRIN] Rozpoczynam drukowanie...")
            
            # Krok 1: Ustaw parametry etykiety
            await self.send_command(CMD_SET_LABEL_TYPE + bytes([self.config.label_type]))
            await self.send_command(CMD_SET_LABEL_DENSITY + bytes([self.config.density]))
            
            # Krok 2: Start drukowania
            await self.send_command(CMD_START_PRINT)
            await self.send_command(CMD_START_PAGE)
            
            # Krok 3: Wyślij dane obrazu
            print("[UPLO] Wysyłanie danych obrazu...")
            await self._send_image_data(img)
            
            # Krok 4: Zakończ drukowanie
            await self.send_command(CMD_END_PAGE)
            await self.send_command(CMD_END_PRINT)
            
            print("[OK] Drukowanie zakończone!")
            return True
            
        except Exception as e:
            print(f"[ERR] Błąd drukowania: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _prepare_image(self, image: Image.Image) -> Image.Image:
        """Przygotowuje obraz do druku (resize, convert to 1-bit)"""
        # Resize do rozmiaru etykiety
        img = image.resize((self.config.width, self.config.height), Image.Resampling.LANCZOS)
        
        # Konwertuj do 1-bit (czarno-białe)
        img = img.convert('1', dither=Image.Dither.FLOYDSTEINBERG)
        
        return img
    
    async def _send_image_data(self, image: Image.Image):
        """Wysyła dane obrazu linia po linii"""
        width, height = image.size
        
        for y in range(height):
            # Pobierz wiersz pikseli
            row_data = []
            for x in range(0, width, 8):
                byte = 0
                for bit in range(8):
                    if x + bit < width:
                        pixel = image.getpixel((x + bit, y))
                        # 0 = czarny (drukuj), 1 = biały (nie drukuj)
                        if pixel == 0:
                            byte |= (1 << (7 - bit))
                row_data.append(byte)
            
            # Wyślij wiersz (komenda + numer linii + dane)
            line_cmd = bytes([0x1b, 0x84]) + bytes([y & 0xFF, (y >> 8) & 0xFF]) + bytes(row_data)
            await self.send_command(line_cmd)
            
            # Progress
            if y % 20 == 0:
                print(f"   Linia {y}/{height} ({int(y/height*100)}%)")


async def create_product_label(
    nazwa: str,
    cena: float,
    sku: str = "",
    barcode: str = "",
    lokalizacja: str = ""
) -> Image.Image:
    """
    Tworzy etykietę produktu
    
    Args:
        nazwa: Nazwa produktu
        cena: Cena w PLN
        sku: SKU/kod produktu
        barcode: Kod kreskowy (EAN/UPC)
        lokalizacja: Lokalizacja w magazynie
    
    Returns:
        Obraz PIL gotowy do druku
    """
    width = 384
    height = 200
    
    # Utwórz białe tło
    img = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(img)
    
    # Czcionki (używamy domyślnych)
    try:
        font_large = ImageFont.truetype("arial.ttf", 24)
        font_medium = ImageFont.truetype("arial.ttf", 18)
        font_small = ImageFont.truetype("arial.ttf", 14)
    except:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Ramka
    draw.rectangle([2, 2, width-2, height-2], outline='black', width=2)
    
    y = 10
    
    # Nazwa produktu (max 2 linie)
    nazwa_lines = []
    words = nazwa.split()
    current_line = ""
    max_width = width - 20
    
    for word in words:
        test_line = current_line + " " + word if current_line else word
        bbox = draw.textbbox((0, 0), test_line, font=font_medium)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            if current_line:
                nazwa_lines.append(current_line)
            current_line = word
        
        if len(nazwa_lines) >= 2:
            break
    
    if current_line and len(nazwa_lines) < 2:
        nazwa_lines.append(current_line)
    
    for line in nazwa_lines:
        bbox = draw.textbbox((0, 0), line, font=font_medium)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, fill='black', font=font_medium)
        y += 25
    
    y += 5
    
    # Linia separatora
    draw.line([10, y, width-10, y], fill='black', width=1)
    y += 10
    
    # Cena (duża)
    cena_text = f"{cena:.2f} PLN"
    bbox = draw.textbbox((0, 0), cena_text, font=font_large)
    x = (width - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), cena_text, fill='black', font=font_large)
    y += 35
    
    # SKU i lokalizacja
    if sku:
        sku_text = f"SKU: {sku}"
        bbox = draw.textbbox((0, 0), sku_text, font=font_small)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), sku_text, fill='black', font=font_small)
        y += 20
    
    if lokalizacja:
        loc_text = f"<i class=mi>location_on</i> {lokalizacja}"
        bbox = draw.textbbox((0, 0), loc_text, font=font_small)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), loc_text, fill='black', font=font_small)
    
    # TODO: Barcode (wymaga biblioteki python-barcode)
    # if barcode:
    #     from barcode import EAN13
    #     from barcode.writer import ImageWriter
    #     ean = EAN13(barcode, writer=ImageWriter())
    #     ...
    
    return img


# ==================== FUNKCJE POMOCNICZE ====================

async def test_print(device_address: Optional[str] = None):
    """Funkcja testowa - drukuje prostą etykietę"""
    printer = NiimbotPrinter(device_address)
    
    try:
        if not await printer.connect():
            return False
        
        # Utwórz testową etykietę
        label = await create_product_label(
            nazwa="TEST ETYKIETA",
            cena=99.99,
            sku="TEST-001",
            lokalizacja="A2-3"
        )
        
        # Zapisz dla podglądu
        label.save("test_label_preview.png")
        print("[SAVE] Zapisano podgląd: test_label_preview.png")
        
        # Drukuj
        success = await printer.print_image(label)
        
        await printer.disconnect()
        return success
        
    except Exception as e:
        print(f"[ERR] Błąd testu: {e}")
        import traceback
        traceback.print_exc()
        await printer.disconnect()
        return False


async def print_product_label(
    nazwa: str,
    cena: float,
    sku: str = "",
    lokalizacja: str = "",
    device_address: Optional[str] = None
) -> bool:
    """
    Drukuje etykietę produktu
    
    Args:
        nazwa: Nazwa produktu
        cena: Cena
        sku: SKU
        lokalizacja: Lokalizacja w magazynie
        device_address: Adres BT drukarki (None = auto-wykryj)
    
    Returns:
        True jeśli sukces
    """
    printer = NiimbotPrinter(device_address)
    
    try:
        if not await printer.connect():
            return False
        
        label = await create_product_label(
            nazwa=nazwa,
            cena=cena,
            sku=sku,
            lokalizacja=lokalizacja
        )
        
        success = await printer.print_image(label)
        await printer.disconnect()
        return success
        
    except Exception as e:
        print(f"[ERR] Błąd drukowania: {e}")
        await printer.disconnect()
        return False


if __name__ == "__main__":
    # Test
    print("=== TEST NIIMBOT B1 ===\n")
    asyncio.run(test_print())
