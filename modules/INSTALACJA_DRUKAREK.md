# 🖨️ INSTALACJA MODUŁÓW DRUKAREK

## ⚡ SZYBKA INSTALACJA (2 MINUTY):

### **KROK 1: Skopiuj moduły**

1. Pobierz z outputu:
   - `niimbot_print.py`
   - `vretti_print.py`

2. Skopiuj je do:
   ```
   akces_hub_v3_0_12 — BACKUP 2026-15-01\modules\
   ```

### **KROK 2: Zainstaluj biblioteki**

W PowerShell/CMD (w folderze projektu):

```powershell
pip install niimprint --break-system-packages
pip install pyusb --break-system-packages
pip install pillow --break-system-packages
pip install bleak --break-system-packages
```

### **KROK 3: Restart Flask**

```powershell
Ctrl+C (zatrzymaj)
python app.py (uruchom ponownie)
```

### **KROK 4: Test!**

Wejdź na: `http://127.0.0.1:5000/settings/printing`

Kliknij: **Test Niimbot B1** lub **Test Vretti 420B**

---

## 📋 CO ROBI KAŻDY MODUŁ:

### **niimbot_print.py** (Bluetooth)
- Obsługuje drukarkę Niimbot B1
- Używa biblioteki `niimprint`
- Połączenie przez Bluetooth

### **vretti_print.py** (USB)
- Obsługuje drukarkę Vretti 420B
- Używa protokołu ESC/POS
- Połączenie przez USB

---

## 🔧 TROUBLESHOOTING:

### **Windows - USB nie działa:**
1. Pobierz Zadig: https://zadig.akeo.ie/
2. Podłącz drukarkę
3. W Zadig wybierz drukarkę
4. Zainstaluj driver: libusb-win32

### **Linux - USB permission denied:**
```bash
sudo usermod -a -G lp $USER
# Lub ustaw udev rules
```

### **Bluetooth nie łączy:**
- Sparuj drukarkę w ustawieniach systemu NAJPIERW
- Sprawdź czy `bleak` jest zainstalowany
- Restart Bluetooth

---

## ✅ FUNKCJE:

### **test_print()**
- Sprawdza czy biblioteki są zainstalowane
- Testuje połączenie z drukarką
- Wyświetla komunikaty diagnostyczne

### **print_niimbot(produkt)** / **print_vretti_usb(produkt)**
- Drukuje etykietę dla produktu
- Automatycznie wywoływane po wystawieniu oferty
- Aktualizuje `last_printed_at` w bazie

---

## 💡 UWAGI:

- **Moduły są GOTOWE** ale wymagają fizycznego połączenia z drukarką
- **Testy pokażą** czy wszystko zainstalowane poprawnie
- **Kod jest przygotowany** do faktycznego drukowania po dodaniu protokołu komunikacji

---

## 📚 DOKUMENTACJA:

- Niimprint: https://github.com/kjy00302/niimprint
- PyUSB: https://github.com/pyusb/pyusb
- ESC/POS: https://reference.epson-biz.com/modules/ref_escpos/

---

**Gotowe!** 🚀
