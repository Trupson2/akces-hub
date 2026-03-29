"""
Magazynier Extensions - Rozszerzenia dla drukarki i ulepszonego importu
========================================================================

Ten moduł dodaje nowe routes do blueprintu magazynier:
- /drukuj/<code> - drukowanie etykiety produktu
- /drukuj/preview/<code> - podgląd etykiety
- /drukarka - panel zarządzania drukarką
- /import/v2 - ulepszony import z inteligentnym parserem

Dodaj do app.py:
    from modules.magazynier_extensions import register_printer_routes
    register_printer_routes(magazynier_bp)
"""

from flask import Blueprint, request, jsonify, redirect
from datetime import datetime

from .database import get_db
from .printer_manager import (
    PrinterManager, ProductLabel, LabelConfig,
    print_product_label_sync, generate_label_preview_sync, scan_printers_sync,
    get_printer_manager, BLEAK_AVAILABLE, IMAGING_AVAILABLE,
    # USB
    print_niimbot_usb_sync,
    # BLE
    print_niimbot_ble_sync,
    # Vretti
    VrettiPrinter, get_vretti_printer, print_vretti_label_sync,
    generate_vretti_preview_sync, list_system_printers_sync
)
from .inventory_utils import import_excel_manifest, SmartQuantityParser


def register_printer_routes(bp: Blueprint):
    """
    Rejestruje routes drukarki w blueprincie magazynier.
    
    Args:
        bp: Blueprint magazynier
    """
    
    # ============================================================
    # PANEL DRUKARKI
    # ============================================================
    
    @bp.route('/drukarka')
    def drukarka_panel():
        """Panel zarządzania drukarką Niimbot"""
        pm = get_printer_manager()
        
        # Pobierz zapisany port COM i adres BT
        from .database import get_config
        com_port = get_config('niimbot_com_port', 'COM5')
        bt_address = get_config('niimbot_bt_address', '')
        
        # Szczegółowe sprawdzenie niimprint
        niimprint_ok = False
        niimprint_usb = False
        niimprint_error = None
        try:
            from niimprint import SerialTransport, PrinterClient
            niimprint_ok = True
            niimprint_usb = True
        except ImportError:
            try:
                from niimprint import BluetoothTransport, PrinterClient
                niimprint_ok = True
            except ImportError as e:
                niimprint_error = f"Brak modułu: {e}"
        except Exception as e:
            niimprint_error = f"Błąd: {e}"
        
        # Sprawdź python-barcode
        try:
            import barcode
            barcode_ok = True
        except ImportError:
            barcode_ok = False
        
        status = {
            "available": pm.is_available(),
            "connected": pm.connected,
            "device": pm.device_name,
            "address": pm.device_address,
            "bleak": BLEAK_AVAILABLE,
            "imaging": IMAGING_AVAILABLE,
            "niimprint": niimprint_ok,
            "niimprint_usb": niimprint_usb,
            "niimprint_error": niimprint_error,
            "barcode": barcode_ok,
            "com_port": com_port
        }
        
        # Skanuj dostępne porty COM
        try:
            import serial.tools.list_ports
            all_ports = list(serial.tools.list_ports.comports())
        except ImportError:
            all_ports = []
        ports_html = ''
        for port in all_ports:
            is_bt = 'Bluetooth' in (port.description or '') or 'BTHENUM' in (port.hwid or '')
            is_current = port.device == com_port
            icon = '📶' if is_bt else '<span class=material-symbols-outlined>power</span>'
            label = f'{icon} {port.device}'
            if is_bt:
                label += ' (Bluetooth)'
            else:
                label += f' ({port.description[:25]})' if port.description else ''
            color = '#22c55e' if is_current else ('#8b5cf6' if is_bt else '#64748b')
            bg = f'{color}22'
            border = color
            check = ' <span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if is_current else ''
            ports_html += f'''<button type="submit" name="com_port" value="{port.device}"
                style="display:flex;align-items:center;gap:8px;width:100%;padding:12px 16px;background:{bg};border:2px solid {border};border-radius:10px;color:{color};font-size:0.95rem;font-weight:600;cursor:pointer;margin-bottom:8px;text-align:left">
                {label}{check}</button>'''

        if not ports_html:
            ports_html = '<div style="color:#ef4444;padding:12px"><span class=material-symbols-outlined style=color:#ef4444>cancel</span> Brak portów COM — podłącz drukarkę USB lub sparuj Bluetooth</div>'

        html = f'''
        <div class="hdr"><h1><span class=material-symbols-outlined>print</span> DRUKARKA</h1><small>Niimbot B1</small></div>

        <div class="card" style="padding:15px">
            <div style="font-weight:600;margin-bottom:12px"><span class=material-symbols-outlined>print</span> Wybierz port drukarki</div>
            <div style="font-size:0.85rem;color:#64748b;margin-bottom:12px">
                Aktualny: <strong style="color:#22c55e">{com_port}</strong>
                {'📶 (Bluetooth)' if any('BTHENUM' in (p.hwid or '') for p in all_ports if p.device == com_port) else '<span class=material-symbols-outlined>power</span> (USB)'}
            </div>
            <form action="/magazyn/drukarka/ustaw-port" method="POST">
                {ports_html}
            </form>
            <div style="font-size:0.75rem;color:#475569;margin-top:8px;line-height:1.5">
                <span class=material-symbols-outlined>lightbulb</span> <strong>Bluetooth</strong>: sparuj Niimbot w Windows → pojawi się port COM<br>
                <span class=material-symbols-outlined>lightbulb</span> <strong>USB</strong>: podłącz kablem → port pojawi się automatycznie
            </div>
        </div>
        
        <div class="card" style="padding:15px;margin-top:10px">
            <div style="font-weight:600;margin-bottom:12px">Status bibliotek</div>
            
            <div class="det-grid">
                <div class="det">
                    <div class="det-l">niimprint (USB)</div>
                    <div class="det-v">{'<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> OK' if status['niimprint_usb'] else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Brak'}</div>
                </div>
                <div class="det">
                    <div class="det-l">niimprint (BT)</div>
                    <div class="det-v">{'<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> OK' if status['niimprint'] else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Brak'}</div>
                </div>
                <div class="det">
                    <div class="det-l">pillow/qrcode</div>
                    <div class="det-v">{'<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> OK' if status['imaging'] else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Brak'}</div>
                </div>
                <div class="det">
                    <div class="det-l">python-barcode</div>
                    <div class="det-v">{'<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> OK' if status['barcode'] else '<span class=material-symbols-outlined>warning</span> Brak'}</div>
                </div>
            </div>
        </div>
        '''
        
        # Szczegółowy status niimprint
        if not status['niimprint']:
            html += f'''
            <div class="card" style="padding:15px;margin-top:10px;border-color:#ef4444">
                <div style="font-weight:600;margin-bottom:12px;color:#ef4444"><span class=material-symbols-outlined>warning</span> Brak biblioteki niimprint</div>
                <div style="color:#64748b;font-size:0.85rem;margin-bottom:10px">
                    {'Błąd: ' + status['niimprint_error'] if status['niimprint_error'] else 'Biblioteka nie jest zainstalowana'}
                </div>
                <div style="font-size:0.8rem;margin-bottom:8px">Zainstaluj (wymaga Python 3.11):</div>
                <div style="background:#1e1e2e;padding:10px;border-radius:8px;font-family:monospace;font-size:0.75rem;margin-bottom:8px">
                    py -3.11 -m pip install niimprint
                </div>
            </div>
            '''
        
        html += f'''
        <div class="card" style="padding:15px;margin-top:10px">
            <div style="font-weight:600;margin-bottom:12px">Status połączenia</div>
            
            <div class="det-grid">
                <div class="det">
                    <div class="det-l">Port USB</div>
                    <div class="det-v" style="color:#22c55e">{com_port}</div>
                </div>
                <div class="det">
                    <div class="det-l">Bluetooth MAC</div>
                    <div class="det-v" style="font-size:0.75rem">{status['address'] or '—'}</div>
                </div>
            </div>
        </div>
        '''
        
        # Instalacja pozostałych brakujących bibliotek
        missing = []
        if not status['imaging']:
            missing.append('pillow qrcode')
        if not status['barcode']:
            missing.append('python-barcode')
        
        if missing:
            html += f'''
            <div class="alert alert-warn">
                <span class=material-symbols-outlined>warning</span> Brakujące biblioteki: {', '.join(missing)}<br>
                <small style="font-family:monospace">pip install {' '.join(missing)} --break-system-packages</small>
            </div>
            '''
        
        # Przyciski
        html += '''
        <a href="/magazyn/drukarka/test" class="btn btn-ok"><span class=material-symbols-outlined>science</span> TEST DRUKU (USB)</a>
        <a href="/magazyn/drukarka/skanuj" class="btn btn-2"><span class=material-symbols-outlined>search</span> Skanuj Bluetooth</a>
        '''
        
        html += '<a href="/magazyn" class="back">← Powrót</a>'
        
        from .magazynier import render
        return render(html)
    
    @bp.route('/drukarka/ustaw-port', methods=['POST'])
    def drukarka_ustaw_port():
        """Zapisuje port COM dla drukarki USB"""
        from .database import set_config
        com_port = request.form.get('com_port', 'COM5').strip().upper()
        set_config('niimbot_com_port', com_port)
        return redirect('/magazyn/drukarka')

    @bp.route('/drukarka/ustaw-bt', methods=['POST'])
    def drukarka_ustaw_bt():
        """Zapisuje adres Bluetooth MAC"""
        from .database import set_config
        bt_address = request.form.get('bt_address', '').strip().upper()
        set_config('niimbot_bt_address', bt_address)
        return redirect('/magazyn/drukarka')

    @bp.route('/drukarka/ustaw-com')
    def drukarka_ustaw_com():
        """Ustawia port COM z ekranu skanowania"""
        from .database import set_config
        com_port = request.args.get('port', 'COM5').strip().upper()
        set_config('niimbot_com_port', com_port)
        return redirect('/magazyn/drukarka')

    @bp.route('/drukarka/skanuj')
    def drukarka_skanuj():
        """Skanuje dostępne drukarki Bluetooth + porty COM"""
        printers = scan_printers_sync()

        # Skanuj też porty COM (USB)
        com_ports = []
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                com_ports.append({
                    'port': p.device,
                    'desc': p.description or p.device,
                    'hwid': p.hwid or ''
                })
        except ImportError:
            pass

        html = '''
        <div class="hdr"><h1><span class=material-symbols-outlined>search</span> SKANOWANIE</h1><small>Szukanie drukarek...</small></div>
        '''

        # Sekcja BLE
        bt_found = printers and len(printers) > 0 and 'error' not in printers[0]
        bt_error = printers and len(printers) > 0 and 'error' in printers[0]

        if bt_found:
            html += '<div style="font-weight:600;margin:15px 0 8px;color:#3b82f6"><span class=material-symbols-outlined>satellite_alt</span> Bluetooth</div>'
            for p in printers:
                html += f'''
                <a href="/magazyn/drukarka/polacz?addr={p['address']}" class="item">
                    <div style="font-size:1.5rem;margin-right:12px"><span class=material-symbols-outlined>print</span></div>
                    <div class="item-info">
                        <div class="item-name">{p['name']}</div>
                        <div class="item-meta">{p['address']}</div>
                    </div>
                    <div class="item-right">
                        <div class="item-qty" style="font-size:0.9rem">RSSI: {p.get('rssi', '?')}</div>
                    </div>
                </a>
                '''
        elif bt_error:
            html += f'''<div class="alert alert-warn" style="margin-top:10px">
                <span class=material-symbols-outlined>satellite_alt</span> Bluetooth: {printers[0]["error"]}</div>'''
        else:
            html += '<div class="alert alert-warn" style="margin-top:10px"><span class=material-symbols-outlined>satellite_alt</span> Nie znaleziono drukarek Bluetooth</div>'

        # Sekcja COM/USB
        if com_ports:
            html += '<div style="font-weight:600;margin:20px 0 8px;color:#22c55e"><span class=material-symbols-outlined>power</span> Porty USB (COM)</div>'
            from .database import set_config, get_config
            current_port = get_config('niimbot_com_port') or 'COM5'
            for cp in com_ports:
                is_current = cp['port'] == current_port
                badge = ' <span style="color:#22c55e;font-size:0.75rem">AKTYWNY</span>' if is_current else ''
                html += f'''
                <a href="/magazyn/drukarka/ustaw-com?port={cp['port']}" class="item" style="{'border:1px solid rgba(34,197,94,0.3);' if is_current else ''}">
                    <div style="font-size:1.5rem;margin-right:12px"><span class=material-symbols-outlined>power</span></div>
                    <div class="item-info">
                        <div class="item-name">{cp['port']}{badge}</div>
                        <div class="item-meta">{cp['desc']}</div>
                    </div>
                    <div class="item-right">
                        <div class="item-qty" style="font-size:0.75rem;color:#64748b">USB</div>
                    </div>
                </a>
                '''
        else:
            html += '<div class="alert" style="margin-top:15px;background:rgba(100,116,139,0.1);color:#94a3b8"><span class=material-symbols-outlined>power</span> Brak portów COM (USB)</div>'

        if not bt_found and not com_ports:
            html += '''
            <div class="card" style="margin-top:15px;background:linear-gradient(135deg,rgba(59,130,246,0.15),rgba(139,92,246,0.15));border:1px solid rgba(59,130,246,0.3)">
                <div style="font-weight:600;margin-bottom:10px"><span class=material-symbols-outlined>lightbulb</span> Wskazówki</div>
                <div style="font-size:0.85rem;color:#94a3b8;line-height:1.6">
                    <strong>USB:</strong> Podłącz Niimbot kablem USB i odśwież stronę<br>
                    <strong>Bluetooth:</strong> Sprawdź czy BT jest włączony w Windows<br>
                    <strong>Telefon:</strong> Użyj oficjalnej apki NIIMBOT
                </div>
            </div>
            '''

        html += '''
        <a href="/magazyn/drukarka/skanuj" class="btn btn-2" style="margin-top:15px"><span class=material-symbols-outlined>sync</span> Skanuj ponownie</a>
        <a href="/magazyn/drukarka" class="back">← Powrót</a>
        '''

        from .magazynier import render
        return render(html)
    
    @bp.route('/drukarka/polacz')
    def drukarka_polacz():
        """Łączy z drukarką"""
        import asyncio
        import threading
        from urllib.parse import quote
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
        
        addr = request.args.get('addr', '')
        
        if not addr:
            return redirect('/magazyn/drukarka?err=Brak adresu drukarki')
        
        pm = get_printer_manager()

        # Zapisz adres BLE — zarówno w PM jak i w konfiguracji
        pm.device_address = addr
        from .database import set_config
        set_config('niimbot_bt_address', addr)

        result_holder = {'success': False, 'error': None}
        
        def connect_in_thread():
            """Uruchom połączenie w osobnym wątku z własną pętlą asyncio"""
            try:
                # Utwórz nową pętlę eventów dla tego wątku
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    # Połącz z timeoutem 15 sekund
                    result = loop.run_until_complete(
                        asyncio.wait_for(pm.connect(addr), timeout=15.0)
                    )
                    result_holder['success'] = result
                except asyncio.TimeoutError:
                    result_holder['error'] = 'Timeout - drukarka nie odpowiada'
                except Exception as e:
                    result_holder['error'] = str(e)
                finally:
                    try:
                        loop.close()
                    except:
                        pass
            except Exception as e:
                result_holder['error'] = str(e)
        
        try:
            # Uruchom w osobnym wątku z timeoutem
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(connect_in_thread)
                try:
                    future.result(timeout=20)  # 20 sekund max
                except FuturesTimeoutError:
                    result_holder['error'] = 'Timeout połączenia'
            
            if result_holder['success']:
                return redirect('/magazyn/drukarka?msg=Połączono!')
            else:
                err = result_holder['error'] or 'Nie udało się połączyć'
                err_clean = str(err).replace("'", "").replace('"', '').replace('\n', ' ')[:50]
                return redirect(f'/magazyn/drukarka?err={quote(err_clean)}')
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = str(e).replace("'", "").replace('"', '').replace('\n', ' ')[:40]
            return redirect(f'/magazyn/drukarka?err={quote(err_msg)}')
    
    @bp.route('/drukarka/rozlacz')
    def drukarka_rozlacz():
        """Rozłącza drukarkę"""
        import asyncio
        
        pm = get_printer_manager()
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(pm.disconnect())
            loop.close()
        except:
            pass
            
        return redirect('/magazyn/drukarka')
    
    @bp.route('/drukarka/test')
    def drukarka_test():
        """Test drukowania - drukuje testową etykietę z diagnostyką"""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
        from io import StringIO
        import sys
        
        pm = get_printer_manager()
        
        if not pm.device_address:
            from .magazynier import render
            return render('''
                <div class="hdr"><h1><span class=material-symbols-outlined>science</span> TEST DRUKU</h1></div>
                <div class="alert alert-err">Najpierw połącz z drukarką (skanuj → połącz)</div>
                <a href="/magazyn/drukarka" class="btn btn-p">← Powrót</a>
            ''')
        
        # Przechwytuj logi
        logs = []
        
        def log_capture(msg):
            logs.append(msg)
            print(msg)
        
        result_holder = {'success': False, 'error': None}
        
        def test_print_thread():
            """Test drukowania w osobnym wątku"""
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # Utwórz testową etykietę
                from .printer_manager import ProductLabel
                
                test_label = ProductLabel(
                    nazwa=f"TEST DRUKU {get_config('brand_name', 'Akces Hub')}",
                    qr_data="TEST:123456",
                    lokalizacja="POLKA-A1",
                    ean="5901234123457"
                )
                
                log_capture(f"<span class=material-symbols-outlined>push_pin</span> Adres drukarki: {pm.device_address}")
                log_capture(f"<span class=material-symbols-outlined>push_pin</span> Nazwa drukarki: {pm.device_name or 'Niimbot'}")
                log_capture(f"<span class=material-symbols-outlined>push_pin</span> Status connected: {pm.connected}")
                
                # Pobierz port COM z konfiguracji
                from .database import get_config
                com_port = get_config('niimbot_com_port', 'COM5')
                log_capture(f"<span class=material-symbols-outlined>push_pin</span> Port USB: {com_port}")
                
                # Sprawdź niimprint
                niimprint_available = False
                serial_transport = None
                try:
                    from niimprint import SerialTransport, PrinterClient
                    niimprint_available = True
                    serial_transport = SerialTransport
                    log_capture("<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> niimprint dostępny (USB)")
                except ImportError:
                    try:
                        from niimprint import BluetoothTransport, PrinterClient
                        niimprint_available = True
                        log_capture("<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> niimprint dostępny (Bluetooth only)")
                    except ImportError:
                        log_capture("<span class=material-symbols-outlined>warning</span> niimprint niedostępny")
                
                if niimprint_available and serial_transport:
                    # Druk przez niimprint USB
                    try:
                        log_capture("<span class=material-symbols-outlined>sync</span> Generuję obraz etykiety...")
                        img = pm._generate_label_image(test_label)
                        log_capture(f"<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Obraz: {img.size[0]}x{img.size[1]} px, mode={img.mode}")
                        
                        # Konwersja
                        target_width = 384
                        if img.size[0] != target_width:
                            ratio = target_width / img.size[0]
                            new_height = int(img.size[1] * ratio)
                            from PIL import Image
                            img = img.resize((target_width, new_height), Image.Resampling.LANCZOS)
                            log_capture(f"<span class=material-symbols-outlined>straighten</span> Przeskalowano do: {img.size[0]}x{img.size[1]}")
                        
                        if img.mode == '1':
                            img = img.convert('L')
                        elif img.mode != 'L':
                            img = img.convert('L')
                        
                        log_capture(f"<span class=material-symbols-outlined>link</span> Łączenie przez USB ({com_port})...")
                        transport = serial_transport(com_port)
                        printer = PrinterClient(transport)
                        
                        log_capture("<span class=material-symbols-outlined>upload</span> Wysyłam do drukarki...")
                        printer.print_image(img, density=3)
                        
                        log_capture("<span class=material-symbols-outlined>power</span> Zamykam połączenie...")
                        transport.close()
                        
                        log_capture("<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> DRUK ZAKOŃCZONY!")
                        result_holder['success'] = True
                    except Exception as e:
                        log_capture(f"<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Błąd niimprint USB: {e}")
                        result_holder['error'] = str(e)
                elif niimprint_available:
                    # Fallback na Bluetooth
                    log_capture("<span class=material-symbols-outlined>warning</span> USB niedostępny, próbuję Bluetooth...")
                    result_holder['error'] = "USB niedostępny - podłącz drukarkę kablem"
                else:
                    log_capture("<span class=material-symbols-outlined style=color:#ef4444>cancel</span> niimprint nie jest zainstalowany")
                    log_capture("<span class=material-symbols-outlined>lightbulb</span> Zainstaluj: py -3.11 -m pip install niimprint")
                    result_holder['error'] = "Brak niimprint"
                
                loop.close()
                
            except Exception as e:
                log_capture(f"<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Krytyczny błąd: {e}")
                result_holder['error'] = str(e)
        
        # Uruchom test
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(test_print_thread)
                future.result(timeout=60)
        except FuturesTimeoutError:
            logs.append("<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Timeout - drukarka nie odpowiada po 60s")
            result_holder['error'] = "Timeout"
        except Exception as e:
            logs.append(f"<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Błąd: {e}")
        
        # Wyświetl wynik
        from .magazynier import render
        
        status_class = "alert-ok" if result_holder['success'] else "alert-err"
        status_text = "<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> SUKCES!" if result_holder['success'] else f"<span class=material-symbols-outlined style=color:#ef4444>cancel</span> BŁĄD: {result_holder['error']}"
        
        logs_html = "<br>".join(logs)
        
        html = f'''
        <div class="hdr"><h1><span class=material-symbols-outlined>science</span> TEST DRUKU</h1></div>
        
        <div class="alert {status_class}">{status_text}</div>
        
        <div class="card" style="padding:15px">
            <div style="font-weight:600;margin-bottom:10px"><span class=material-symbols-outlined>assignment</span> Logi:</div>
            <div style="font-family:monospace;font-size:0.8rem;background:#0a0a0f;padding:10px;border-radius:5px;white-space:pre-wrap">
{logs_html}
            </div>
        </div>
        
        <a href="/magazyn/drukarka/test" class="btn btn-p"><span class=material-symbols-outlined>sync</span> Powtórz test</a>
        <a href="/magazyn/drukarka" class="btn btn-2">← Powrót</a>
        '''
        
        return render(html)
    
    # ============================================================
    # DRUKOWANIE ETYKIET
    # ============================================================
    
    @bp.route('/drukuj/<path:code>')
    def drukuj_etykiete(code):
        """Strona drukowania etykiety - MOBILNA + DESKTOP"""
        conn = get_db()
        p = conn.execute('SELECT * FROM produkty WHERE ean=? OR asin=? OR kod_magazynowy=? OR id=?', (code, code, str(code).upper(), code)).fetchone()

        if not p:
            from .magazynier import render
            return render('<div class="alert alert-err">Produkt nie znaleziony</div><a href="/magazyn" class="back">← Powrót</a>')

        p = dict(p)

        # Pobierz dane palety (koszt/szt, nazwa)
        paleta_nazwa = ''
        koszt_szt = 0
        if p.get('paleta_id'):
            from .magazynier import _paleta_koszt_szt
            koszt_szt = _paleta_koszt_szt(conn, p['paleta_id'])
            pal_row = conn.execute('SELECT nazwa FROM palety WHERE id=?', (p['paleta_id'],)).fetchone()
            if pal_row:
                paleta_nazwa = pal_row['nazwa'] or ''

        # Sprawdź czy jest powiązana oferta Allegro
        oferta = conn.execute('SELECT allegro_id FROM oferty WHERE produkt_id = ?', (p['id'],)).fetchone()

        # Przygotuj dane do etykiety
        nazwa_skrocona = p['nazwa'][:30] if p['nazwa'] else f"Produkt #{p['id']}"

        # QR data - URL do produktu (skanowanie telefonem otwiera stronę)
        kod_mag = p.get('kod_magazynowy', '') or ''
        if not kod_mag:
            kod_mag = code
        product_code = kod_mag or p.get('ean') or p.get('asin') or str(p['id'])
        from .database import get_config as _gc
        _ngrok = _gc('ngrok_domain', '')
        _base = f"https://{_ngrok}" if _ngrok else request.host_url.rstrip('/')
        qr_data = f"{_base}/magazyn/produkt/{product_code}"

        # Pobierz port COM i adres BT
        from .database import get_config
        com_port = get_config('niimbot_com_port', 'COM5')
        bt_address = get_config('niimbot_bt_address', '')

        # Generuj podgląd
        preview = generate_label_preview_sync(
            nazwa=nazwa_skrocona,
            qr_data=qr_data,
            lokalizacja=p['lokalizacja'] or '',
            ean=p['ean'] or '',
            ilosc=p.get('ilosc', 1) or 1,
            dostawca=p.get('dostawca', '') or '',
            data_zakupu=p.get('data_zakupu', '') or p.get('data_dodania', '') or '',
            paleta=paleta_nazwa,
            koszt_szt=koszt_szt,
            cena_allegro=float(p.get('cena_allegro', 0) or 0),
            kod_magazynowy=p.get('kod_magazynowy', '') or ''
        )
        
        # Escape single quotes for JS
        _js_nazwa = (p['nazwa'][:35] if p['nazwa'] else '').replace("'", "\\'")
        _js_qr = qr_data.replace("'", "\\'")
        _js_lok = (p['lokalizacja'] or '').replace("'", "\\'")
        _js_ean = p['ean'] or ''
        _js_dost = (p.get('dostawca', '') or '').replace("'", "\\'")
        _js_dz = (p.get('data_zakupu', '') or p.get('data_dodania', '') or '').replace("'", "\\'")
        _js_pal = paleta_nazwa.replace("'", "\\'")
        _js_km = kod_mag.replace("'", "\\'")

        html = f'''
        <div class="hdr"><h1><span class=material-symbols-outlined>print</span> DRUKUJ</h1><small>{p['nazwa'][:40]}</small></div>

        <!-- PODGLĄD ETYKIETY -->
        <div class="card" style="padding:15px;text-align:center;background:#fff;margin-bottom:15px">
        '''

        if preview:
            html += f'<img src="{preview}" style="max-width:100%;border:1px solid #ddd">'
        else:
            html += '<div style="color:#666;padding:20px">Podgląd niedostępny</div>'

        html += f'''
        </div>

        <!-- INFO -->
        <div class="card" style="padding:12px;margin-bottom:15px">
            <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                <span style="color:#64748b">EAN:</span>
                <span>{p['ean'] or '—'}</span>
            </div>
            <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                <span style="color:#64748b">Lokalizacja:</span>
                <span>{p['lokalizacja'] or '—'}</span>
            </div>
            <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                <span style="color:#64748b">Paleta:</span>
                <span>{paleta_nazwa or '—'}</span>
            </div>
            <div style="display:flex;justify-content:space-between">
                <span style="color:#64748b">Drukarka:</span>
                <span style="color:#22c55e">Niimbot ({('BLE ' + bt_address[:8] + '...') if bt_address else com_port})</span>
            </div>
        </div>

        <!-- STATUS -->
        <div id="printStatus" style="display:none;margin-bottom:12px;padding:15px;border-radius:12px;text-align:center;font-weight:600;font-size:1.1rem"></div>

        <!-- DRUKUJ 1 -->
        <button id="btnPrint1" onclick="drukujEtykiete(1)" class="btn btn-ok" style="font-size:1.5rem;padding:20px;width:100%;margin-bottom:10px">
            <span class=material-symbols-outlined>print</span> DRUKUJ 1 ETYKIETĘ
        </button>

        <!-- KOPIE -->
        <div style="display:flex;gap:8px;margin-bottom:15px">
            <button onclick="drukujEtykiete(2)" class="btn btn-2" style="flex:1;padding:12px">×2</button>
            <button onclick="drukujEtykiete(3)" class="btn btn-2" style="flex:1;padding:12px">×3</button>
            <button onclick="drukujEtykiete(5)" class="btn btn-2" style="flex:1;padding:12px">×5</button>
            <button onclick="drukujEtykiete(10)" class="btn btn-2" style="flex:1;padding:12px">×10</button>
        </div>

        <a href="/magazyn/drukarka" class="btn btn-2" style="margin-top:8px"><span class=material-symbols-outlined>build</span> Ustawienia drukarki</a>
        <a href="/magazyn/produkt/{code}" class="back">← Powrót</a>

        <script>
        function drukujEtykiete(copies) {{
            const btn = document.getElementById('btnPrint1');
            const status = document.getElementById('printStatus');

            document.querySelectorAll('button').forEach(b => b.disabled = true);
            btn.innerHTML = '⏳ Drukuję...';
            btn.style.opacity = '0.6';
            status.style.display = 'none';

            const body = new URLSearchParams({{
                nazwa: '{_js_nazwa}',
                qr_data: '{_js_qr}',
                lokalizacja: '{_js_lok}',
                ean: '{_js_ean}',
                printer_type: '{'niimbot_ble' if bt_address else 'niimbot_usb'}',
                copies: copies,
                ilosc: '{p.get("ilosc", 1) or 1}',
                dostawca: '{_js_dost}',
                data_zakupu: '{_js_dz}',
                paleta: '{_js_pal}',
                cena_allegro: '{float(p.get("cena_allegro", 0) or 0)}',
                kod_magazynowy: '{_js_km}'
            }});

            fetch('/magazyn/drukuj/{code}/wykonaj', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest'}},
                body: body,
                redirect: 'manual'
            }})
            .then(r => r.json().catch(() => ({{}})))
            .then(data => {{
                status.style.display = 'block';
                if (data.success) {{
                    status.style.background = '#22c55e22';
                    status.style.border = '2px solid #22c55e';
                    status.style.color = '#22c55e';
                    status.innerHTML = '<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Wydrukowano ' + copies + (copies === 1 ? ' etykietę' : ' etykiet') + '!';
                }} else {{
                    status.style.background = '#ef444422';
                    status.style.border = '2px solid #ef4444';
                    status.style.color = '#ef4444';
                    status.innerHTML = '<span class=material-symbols-outlined style=color:#ef4444>cancel</span> ' + (data.message || 'Błąd druku — sprawdź drukarkę');
                }}
            }})
            .catch(err => {{
                status.style.display = 'block';
                status.style.background = '#ef444422';
                status.style.border = '2px solid #ef4444';
                status.style.color = '#ef4444';
                status.innerHTML = '<span class=material-symbols-outlined style=color:#ef4444>cancel</span> Błąd połączenia: ' + err.message;
            }})
            .finally(() => {{
                document.querySelectorAll('button').forEach(b => b.disabled = false);
                btn.innerHTML = '<span class=material-symbols-outlined>print</span> DRUKUJ 1 ETYKIETĘ';
                btn.style.opacity = '1';
            }});
        }}
        </script>
        '''

        from .magazynier import render
        return render(html)

    @bp.route('/drukuj/<path:code>/wykonaj', methods=['POST'])
    def drukuj_wykonaj(code):
        """Wykonuje drukowanie etykiety"""
        from flask import jsonify
        import traceback as _tb

        try:
            nazwa = request.form.get('nazwa', '')
            qr_data = request.form.get('qr_data', '')
            lokalizacja = request.form.get('lokalizacja', '')
            ean = request.form.get('ean', '')
            copies = int(request.form.get('copies', 1))
            printer_type = request.form.get('printer_type', 'niimbot_usb')
            ilosc = int(request.form.get('ilosc', 1) or 1)
            dostawca = request.form.get('dostawca', '')
            data_zakupu = request.form.get('data_zakupu', '')
            paleta = request.form.get('paleta', '')
            cena_allegro = float(request.form.get('cena_allegro', 0) or 0)
            kod_magazynowy = request.form.get('kod_magazynowy', '')
        except Exception as parse_err:
            return jsonify({'success': False, 'message': f'Blad parsowania: {parse_err}'}), 400

        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        # Pobierz port COM z konfiguracji
        from .database import get_config
        com_port = get_config('niimbot_com_port', 'COM5')

        try:
            if printer_type == 'niimbot_ble':
                bt_addr = get_config('niimbot_bt_address', '')
                result = print_niimbot_ble_sync(
                    nazwa=nazwa,
                    qr_data=qr_data,
                    lokalizacja=lokalizacja,
                    ean=ean,
                    bt_address=bt_addr,
                    copies=copies,
                    ilosc=ilosc,
                    dostawca=dostawca,
                    data_zakupu=data_zakupu,
                    paleta=paleta,
                    cena_allegro=cena_allegro,
                    kod_magazynowy=kod_magazynowy
                )
            elif printer_type == 'niimbot_bt':
                from .printer_manager import print_niimbot_bt_sync
                bt_addr = get_config('niimbot_bt_address', '')
                result = print_niimbot_bt_sync(
                    nazwa=nazwa,
                    qr_data=qr_data,
                    lokalizacja=lokalizacja,
                    ean=ean,
                    bt_address=bt_addr,
                    copies=copies,
                    ilosc=ilosc,
                    dostawca=dostawca,
                    data_zakupu=data_zakupu,
                    paleta=paleta,
                    cena_allegro=cena_allegro,
                    kod_magazynowy=kod_magazynowy
                )
            elif printer_type == 'niimbot_usb':
                result = print_niimbot_usb_sync(
                    nazwa=nazwa,
                    qr_data=qr_data,
                    lokalizacja=lokalizacja,
                    ean=ean,
                    com_port=com_port,
                    copies=copies,
                    ilosc=ilosc,
                    dostawca=dostawca,
                    data_zakupu=data_zakupu,
                    paleta=paleta,
                    cena_allegro=cena_allegro,
                    kod_magazynowy=kod_magazynowy
                )
            elif printer_type == 'vretti':
                system_printer = request.form.get('system_printer', '')
                result = print_vretti_label_sync(
                    nazwa=nazwa,
                    qr_data=qr_data,
                    lokalizacja=lokalizacja,
                    ean=ean,
                    printer_name=system_printer if system_printer else None,
                    copies=copies
                )
            else:
                result = print_product_label_sync(
                    nazwa=nazwa,
                    qr_data=qr_data,
                    lokalizacja=lokalizacja,
                    ean=ean,
                    copies=copies
                )
        except Exception as print_err:
            _tb.print_exc()
            result = {'success': False, 'message': f'Blad druku: {print_err}'}

        # Zapisz w historii
        if result.get('success'):
            try:
                conn = get_db()
                p = conn.execute('SELECT id FROM produkty WHERE ean=? OR asin=? OR kod_magazynowy=? OR id=?', (code, code, str(code).upper(), code)).fetchone()
                if p:
                    from .database import execute_db
                    execute_db('''INSERT INTO historia_produktu (produkt_id, akcja, opis, data)
                                 VALUES (?, 'drukowano', ?, datetime('now', 'localtime'))''',
                              (p['id'], f'Wydrukowano {copies} etykiet'))
            except Exception:
                pass

        # AJAX → JSON response
        if is_ajax:
            return jsonify(result)

        # Fallback → redirect (stary flow)
        if result['success']:
            return redirect(f'/magazyn/produkt/{code}?msg=Wydrukowano {copies} etykiet!')
        else:
            from .magazynier import render
            html = f'''
            <div class="hdr"><h1>BLAD DRUKU</h1></div>
            <div class="alert alert-err">{result['message']}</div>
            <a href="/magazyn/drukuj/{code}" class="btn btn-p">Sprobuj ponownie</a>
            <a href="/magazyn/drukarka" class="btn btn-2">Ustawienia drukarki</a>
            '''
            return render(html)

    @bp.route('/drukuj-szybko/<path:code>')
    def drukuj_szybko(code):
        """Szybkie drukowanie - 1 klik = 1 etykieta (dla telefonu)"""
        conn = get_db()
        p = conn.execute('SELECT * FROM produkty WHERE ean=? OR asin=? OR kod_magazynowy=? OR id=?', (code, code, str(code).upper(), code)).fetchone()
        # NIE zamykaj pooled connection!

        if not p:
            from .magazynier import render
            return render('<div class="alert alert-err">Produkt nie znaleziony</div><a href="/magazyn" class="back">Powrot</a>')
        
        p = dict(p)
        
        # Przygotuj dane etykiety
        nazwa_skrocona = p['nazwa'][:30] if p['nazwa'] else f"Produkt #{p['id']}"
        kod_mag = p.get('kod_magazynowy', '') or ''
        qr_data = kod_mag if kod_mag else f"MAG:{p['ean'] or p['asin'] or p['id']}"

        # Pobierz port COM
        from .database import get_config
        com_port = get_config('niimbot_com_port', 'COM5')

        # DRUKUJ!
        result = print_niimbot_usb_sync(
            nazwa=nazwa_skrocona,
            qr_data=qr_data,
            lokalizacja=p.get('lokalizacja', '') or '',
            ean=p.get('ean', '') or '',
            com_port=com_port,
            copies=1,
            ilosc=p.get('ilosc', 1) or 1,
            dostawca=p.get('dostawca', '') or '',
            data_zakupu=p.get('data_zakupu', '') or p.get('data_dodania', '') or '',
            kod_magazynowy=kod_mag
        )
        
        if result['success']:
            # Zapisz w historii
            try:
                from .database import execute_db
                execute_db('''INSERT INTO historia_produktu (produkt_id, akcja, opis, data) 
                             VALUES (?, 'drukowano', ?, datetime('now', 'localtime'))''',
                          (p['id'], 'Wydrukowano 1 etykietę'))
            except:
                pass
            
            return redirect(f'/magazyn/produkt/{code}?msg=<span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Wydrukowano!')
        else:
            from .magazynier import render
            html = f'''
            <div class="hdr"><h1><span class=material-symbols-outlined style=color:#ef4444>cancel</span> BŁĄD DRUKU</h1></div>
            <div class="alert alert-err">{result['message']}</div>
            <a href="/magazyn/drukuj-szybko/{code}" class="btn btn-ok" style="font-size:1.3rem;padding:15px"><span class=material-symbols-outlined>sync</span> SPRÓBUJ PONOWNIE</a>
            <a href="/magazyn/drukarka" class="btn btn-2"><span class=material-symbols-outlined>build</span> Ustawienia drukarki</a>
            <a href="/magazyn/produkt/{code}" class="back">← Powrót</a>
            '''
            return render(html)
    
    @bp.route('/etykieta/<path:code>.png')
    def pobierz_etykiete(code):
        """Pobiera etykietę jako PNG - do druku przez apkę Niimbot na telefonie"""
        conn = get_db()
        p = conn.execute('SELECT * FROM produkty WHERE ean=? OR asin=? OR kod_magazynowy=? OR id=?', (code, code, str(code).upper(), code)).fetchone()

        if not p:
            return "Produkt nie znaleziony", 404

        p = dict(p)

        # Pobierz dane palety
        paleta_nazwa = ''
        koszt_szt = 0
        if p.get('paleta_id'):
            from .magazynier import _paleta_koszt_szt
            koszt_szt = _paleta_koszt_szt(conn, p['paleta_id'])
            pal_row = conn.execute('SELECT nazwa FROM palety WHERE id=?', (p['paleta_id'],)).fetchone()
            if pal_row:
                paleta_nazwa = pal_row['nazwa'] or ''

        # Sprawdź czy jest oferta Allegro
        oferta = conn.execute('SELECT allegro_id FROM oferty WHERE produkt_id = ?', (p['id'],)).fetchone()

        # Przygotuj dane etykiety
        nazwa_skrocona = p['nazwa'][:30] if p['nazwa'] else f"Produkt #{p['id']}"
        kod_mag = p.get('kod_magazynowy', '') or ''
        qr_data = kod_mag if kod_mag else f"MAG:{p['ean'] or p['asin'] or p['id']}"

        # Generuj obraz etykiety
        from .printer_manager import get_printer_manager, ProductLabel
        from PIL import Image
        import io

        pm = get_printer_manager()
        label = ProductLabel(
            nazwa=nazwa_skrocona,
            qr_data=qr_data,
            lokalizacja=p.get('lokalizacja', '') or '',
            ean=p.get('ean', '') or '',
            ilosc=p.get('ilosc', 1) or 1,
            dostawca=p.get('dostawca', '') or '',
            data_zakupu=p.get('data_zakupu', '') or p.get('data_dodania', '') or '',
            paleta=paleta_nazwa,
            koszt_szt=koszt_szt,
            cena_allegro=0,
            kod_magazynowy=kod_mag,
            stan_przyjecia=p.get('stan_przyjecia', '') or p.get('klasa_jakosci', '') or ''
        )
        
        try:
            img = pm._generate_label_image(label)
            
            # Konwertuj do RGB dla lepszej kompatybilności
            if img.mode != 'RGB':
                img_rgb = Image.new('RGB', img.size, 'white')
                img_rgb.paste(img.convert('L'))
                img = img_rgb
            
            # Zapisz do bufora
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            
            # Zwróć jako plik do pobrania
            from flask import Response
            filename = f"etykieta_{p['ean'] or p['asin'] or p['id']}.png"
            return Response(
                buffer.getvalue(),
                mimetype='image/png',
                headers={
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache',
                    'Expires': '0'
                }
            )
        except Exception as e:
            return f"Błąd generowania etykiety: {e}", 500
    
    @bp.route('/etykieta-mobilna/<path:code>')
    def etykieta_mobilna(code):
        """Strona mobilna do pobrania etykiety na telefon"""
        conn = get_db()
        p = conn.execute('SELECT * FROM produkty WHERE ean=? OR asin=? OR kod_magazynowy=? OR id=?', (code, code, str(code).upper(), code)).fetchone()

        if not p:
            from .magazynier import render
            return render('<div class="alert alert-err">Produkt nie znaleziony</div><a href="/magazyn" class="back">← Powrót</a>')

        p = dict(p)

        # Pobierz dane palety
        paleta_nazwa = ''
        koszt_szt = 0
        if p.get('paleta_id'):
            from .magazynier import _paleta_koszt_szt
            koszt_szt = _paleta_koszt_szt(conn, p['paleta_id'])
            pal_row = conn.execute('SELECT nazwa FROM palety WHERE id=?', (p['paleta_id'],)).fetchone()
            if pal_row:
                paleta_nazwa = pal_row['nazwa'] or ''

        # Generuj podgląd
        nazwa_skrocona = p['nazwa'][:30] if p['nazwa'] else f"Produkt #{p['id']}"
        qr_data = f"MAG:{p['ean'] or p['asin'] or p['id']}"

        from .magazynier import _format_stan_label
        stan_label = _format_stan_label(p.get('stan_przyjecia', ''), p.get('klasa_jakosci', ''))

        preview = generate_label_preview_sync(
            nazwa=nazwa_skrocona,
            qr_data=qr_data,
            lokalizacja=p['lokalizacja'] or '',
            ean=p['ean'] or '',
            ilosc=p.get('ilosc', 1) or 1,
            dostawca=p.get('dostawca', '') or '',
            data_zakupu=p.get('data_zakupu', '') or p.get('data_dodania', '') or '',
            paleta=paleta_nazwa,
            koszt_szt=koszt_szt,
            cena_allegro=float(p.get('cena_allegro', 0) or 0),
            stan_przyjecia=stan_label
        )
        
        html = f'''
        <div class="hdr"><h1><span class=material-symbols-outlined>smartphone</span> ETYKIETA</h1><small>{p['nazwa'][:35]}</small></div>
        
        <!-- PODGLĄD -->
        <div class="card" style="padding:15px;text-align:center;background:#fff;margin-bottom:15px">
        '''
        
        if preview:
            html += f'<img src="{preview}" style="max-width:100%;border:1px solid #ddd">'
        else:
            html += '<div style="color:#666;padding:20px">Podgląd niedostępny</div>'
        
        html += f'''
        </div>
        
        <!-- INSTRUKCJA -->
        <div class="card" style="padding:15px;margin-bottom:15px">
            <div style="font-weight:600;margin-bottom:10px"><span class=material-symbols-outlined>assignment</span> Jak wydrukować:</div>
            <div style="font-size:0.9rem;color:#64748b;line-height:1.6">
                1. Kliknij <b>"Pobierz etykietę"</b> poniżej<br>
                2. Otwórz aplikację <b>NIIMBOT</b> na telefonie<br>
                3. Wybierz pobrany obrazek<br>
                4. Drukuj! <span class=material-symbols-outlined>print</span>
            </div>
        </div>
        
        <!-- DUŻY PRZYCISK POBIERANIA -->
        <a href="/magazyn/etykieta/{code}.png" 
           class="btn btn-ok" 
           style="font-size:1.4rem;padding:20px;width:100%;display:block;text-align:center;margin-bottom:15px"
           download="etykieta_{p['ean'] or p['id']}.png">
            <span class=material-symbols-outlined>download</span> POBIERZ ETYKIETĘ
        </a>
        
        <div style="text-align:center;color:#64748b;font-size:0.85rem;margin-bottom:20px">
            Plik PNG zostanie zapisany w "Pobrane"
        </div>
        
        <a href="/magazyn/produkt/{code}" class="back">← Powrót do produktu</a>
        '''
        
        from .magazynier import render
        return render(html)
    
    # ============================================================
    # ULEPSZONY IMPORT EXCEL (V2)
    # ============================================================

    @bp.route('/import/v2', methods=['GET', 'POST'])
    def import_v2():
        """Ulepszony import Excel z inteligentnym parserem"""
        from .magazynier import render
        
        if request.method == 'GET':
            # Pobierz listę palet z nazwami
            from .database import get_db
            conn = get_db()
            palety_lista = conn.execute('''
                SELECT id, nazwa, dostawca, data_zakupu, ilosc_produktow 
                FROM palety 
                ORDER BY data_zakupu DESC, nazwa
            ''').fetchall()
            
            # Dropdown opcji palet
            palety_options = '<option value=""> Nowa paleta (auto-tworzenie)</option>'
            for paleta in palety_lista:
                nazwa_display = paleta['nazwa'] or f"Paleta #{paleta['id']}"
                info = f"{paleta['dostawca'] or '?'} | {paleta['data_zakupu'] or '?'} | {paleta['ilosc_produktow']} prod."
                pid = paleta['id']
                palety_options += f'<option value="{pid}">{nazwa_display} ({info})</option>'
            
            html = f'''
            <div class="hdr"><h1><span class=material-symbols-outlined>download</span> IMPORT V2</h1><small>Inteligentny parser</small></div>
            
            <div class="card" style="padding:15px">
                <div style="font-weight:600;margin-bottom:12px">Ulepszony import Excel</div>
                <div style="color:#64748b;font-size:0.85rem;margin-bottom:15px">
                    <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Auto-wykrywanie kolumn ilości<br>
                    <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Obsługa formatów: "5 szt.", "5.0", "qty: 5"<br>
                    <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Auto-detekcja dostawcy<br>
                    <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Auto-tworzenie palety<br>
                    <span class=material-symbols-outlined style=color:#22c55e>check_circle</span> Statystyki parsowania
                </div>
                
                <form action="/magazyn/import/v2" method="POST" enctype="multipart/form-data">
                    <div class="form-group">
                        <label>Plik Excel (.xlsx)</label>
                        <input type="file" name="file" accept=".xlsx,.xls" class="form-ctrl" required>
                    </div>
                    
                    <div class="form-group">
                        <label>Dostawca (opcjonalnie - auto-wykrywany)</label>
                        <input type="text" name="dostawca" class="form-ctrl" placeholder="np. Jobalots, Warrington">
                        <small style="color:#64748b;font-size:0.75rem;margin-top:4px;display:block">
                            Zostaw puste jeśli chcesz auto-detekcję z pliku
                        </small>
                    </div>
                    
                    <div class="form-group">
                        <label>Paleta</label>
                        <select name="paleta_id" class="form-ctrl" id="paleta-import-select" onchange="togglePaletaNowa()">
                            {palety_options}
                        </select>
                        <small style="color:#64748b;font-size:0.75rem;margin-top:4px;display:block">
                            <span class=material-symbols-outlined>auto_awesome</span> Nowa paleta = automatycznie stworzy paletę z nazwą "{'{'}Dostawca{'}'} {'{'}Data{'}'}"
                        </small>
                    </div>
                    
                    <label style="display:flex;align-items:center;gap:8px;margin-bottom:15px">
                        <input type="checkbox" name="update_existing" checked>
                        <span>Aktualizuj istniejące produkty (dodaj ilości)</span>
                    </label>
                    
                    <button type="submit" class="btn btn-ok"><span class=material-symbols-outlined>download</span> IMPORTUJ</button>
                </form>
            </div>
            
            <a href="/magazyn/import" class="btn btn-2"><span class=material-symbols-outlined>download</span> Klasyczny import</a>
            <a href="/magazyn" class="back">← Powrót</a>
            '''
            return render(html)
        
        # POST - wykonaj import
        if 'file' not in request.files:
            return render('<div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/import/v2" class="back">← Powrót</a>')
        
        file = request.files['file']
        if file.filename == '':
            return render('<div class="alert alert-err">Nie wybrano pliku</div><a href="/magazyn/import/v2" class="back">← Powrót</a>')
        
        dostawca = request.form.get('dostawca', '')
        paleta_id = request.form.get('paleta_id', '')
        paleta_id = int(paleta_id) if paleta_id else None
        update_existing = 'update_existing' in request.form
        
        # Wykonaj import
        result = import_excel_manifest(
            file_obj=file,
            dostawca=dostawca,
            paleta_id=paleta_id,
            update_existing=update_existing
        )
        
        # Pokaż wyniki
        html = f'''
        <div class="hdr"><h1>{'<span class=material-symbols-outlined style=color:#22c55e>check_circle</span>' if result['success'] else '<span class=material-symbols-outlined style=color:#ef4444>cancel</span>'} IMPORT ZAKOŃCZONY</h1></div>
        '''
        
        if result['success']:
            html += f'''
            <div class="alert alert-ok">
                Dodano: {result['added']} | Zaktualizowano: {result['updated']}
            </div>
            '''
            
            # Link do palety jeśli została utworzona
            if result.get('paleta_id'):
                html += f'''
                <div class="card" style="padding:15px;margin-top:15px;background:rgba(139,92,246,0.1);border:2px solid #8b5cf6">
                    <div style="font-weight:600;margin-bottom:8px;color:#8b5cf6"><span class=material-symbols-outlined>inventory_2</span> PALETA UTWORZONA</div>
                    <a href="/magazyn/paleta/{result['paleta_id']}" class="btn btn-ok" style="width:100%">
                        Zobacz paletę
                    </a>
                </div>
                '''
        else:
            html += '<div class="alert alert-err">Import nie powiódł się</div>'
        
        # Statystyki parsowania ilości
        qty_stats = result.get('quantity_stats', {})
        if qty_stats.get('total_parsed', 0) > 0:
            html += f'''
            <div class="card" style="padding:15px">
                <div style="font-weight:600;margin-bottom:12px"><span class=material-symbols-outlined>bar_chart</span> Statystyki parsowania ilości</div>
                <div class="det-grid">
                    <div class="det">
                        <div class="det-l">Przetworzone</div>
                        <div class="det-v">{qty_stats['total_parsed']}</div>
                    </div>
                    <div class="det">
                        <div class="det-l">Wysoka pewność</div>
                        <div class="det-v green">{qty_stats['high_confidence']}</div>
                    </div>
                    <div class="det">
                        <div class="det-l">Niska pewność</div>
                        <div class="det-v" style="color:#eab308">{qty_stats['low_confidence']}</div>
                    </div>
                </div>
            '''
            
            methods = qty_stats.get('methods', {})
            if methods:
                html += '<div style="margin-top:12px;font-size:0.85rem;color:#64748b">Metody: '
                html += ', '.join([f'{m}: {c}' for m, c in methods.items()])
                html += '</div>'
                
            html += '</div>'
        
        # Szczegóły
        if result.get('details'):
            html += '<div class="section"><span class=material-symbols-outlined>assignment</span> Szczegóły</div><div class="card" style="padding:15px">'
            for detail in result['details'][:10]:  # Max 10
                html += f'<div style="font-size:0.85rem;color:#64748b;margin-bottom:4px">{detail}</div>'
            html += '</div>'
        
        # Błędy
        if result.get('errors'):
            html += '<div class="section" style="color:#ef4444"><span class=material-symbols-outlined style=color:#ef4444>cancel</span> Błędy</div><div class="card" style="padding:15px">'
            for error in result['errors'][:5]:  # Max 5
                html += f'<div style="font-size:0.85rem;color:#ef4444;margin-bottom:4px">{error[:200]}</div>'
            html += '</div>'
        
        html += '''
        <a href="/magazyn/import/v2" class="btn btn-p"><span class=material-symbols-outlined>download</span> Importuj kolejny</a>
        <a href="/magazyn" class="btn btn-2"><span class=material-symbols-outlined>inventory_2</span> Magazyn</a>
        <a href="/magazyn" class="back">← Powrót</a>
        '''
        
        return render(html)
    
    # ============================================================
    # API ENDPOINTS (JSON)
    # ============================================================
    
    @bp.route('/api/print', methods=['POST'])
    def api_print():
        """API endpoint do drukowania"""
        data = request.get_json() or {}
        
        result = print_product_label_sync(
            nazwa=data.get('nazwa', 'Produkt'),
            qr_data=data.get('qr_data', ''),
            lokalizacja=data.get('lokalizacja', ''),
            ean=data.get('ean', ''),
            copies=int(data.get('copies', 1))
        )
        
        return jsonify(result)
    
    @bp.route('/api/printer/status')
    def api_printer_status():
        """API status drukarki"""
        pm = get_printer_manager()
        return jsonify({
            "available": pm.is_available(),
            "connected": pm.connected,
            "device": pm.device_name,
            "address": pm.device_address
        })
    
    @bp.route('/api/printer/scan')
    def api_printer_scan():
        """API skanowanie drukarek"""
        printers = scan_printers_sync()
        return jsonify({"printers": printers})
    
    print("[OK] Printer routes registered")


# ============================================================
# PATCH DO WIDOKU PRODUKTU - dodaje przycisk drukowania
# ============================================================

PRINT_BUTTON_HTML = '''
<a href="/magazyn/drukuj/{code}" class="btn btn-2" style="background:#8b5cf6">
    <span class=material-symbols-outlined>print</span> DRUKUJ ETYKIETĘ
</a>
'''

def get_print_button(product_code: str) -> str:
    """Zwraca HTML przycisku drukowania"""
    return PRINT_BUTTON_HTML.format(code=product_code)
