"""
Akces Hub - Moduły
Lazy imports — moduły ładowane na żądanie, nie blokują startu.
"""

from .database import init_db, get_db, get_config, set_config, query_db, execute_db
from .utils import (
    get_amazon_image_url,
    oblicz_cene_allegro,
    generuj_opis_ai,
    is_code,
    DOSTAWCY,
    ALLEGRO_PROWIZJE
)

# Blueprinty — try/except żeby brak zależności nie blokował startu
try:
    from .magazynier import magazynier_bp, get_stats as mag_stats
except ImportError as e:
    print(f"⚠️ magazynier: {e}")
    magazynier_bp = None
    mag_stats = lambda: {}

try:
    from .paletomat import paletomat_bp, get_stats as pal_stats
except ImportError as e:
    print(f"⚠️ paletomat: {e}")
    paletomat_bp = None
    pal_stats = lambda: {}

try:
    from .telegram_bot import (
        telegram_bp,
        send_telegram,
        bot_status,
        start_bot,
        stop_bot,
        alert_sprzedaz,
        alert_niski_stan,
        alert_nowa_oferta,
        raport_dzienny
    )
except ImportError as e:
    print(f"⚠️ telegram_bot: {e}")
    telegram_bp = None
    send_telegram = lambda *a, **k: None
    bot_status = lambda: {'running': False}
    start_bot = stop_bot = lambda: None
    alert_sprzedaz = alert_niski_stan = alert_nowa_oferta = raport_dzienny = lambda *a, **k: None

try:
    from .allegro_api import (
        allegro_bp,
        is_configured as allegro_configured,
        is_authenticated as allegro_authenticated,
        sync_orders as allegro_sync,
        search_categories,
        get_category_parameters,
        build_offer_parameters,
        build_offer_parameters_ai,
        extract_parameters_with_ai
    )
except ImportError as e:
    print(f"⚠️ allegro_api: {e}")
    allegro_bp = None
    allegro_configured = allegro_authenticated = lambda: False
    allegro_sync = lambda *a, **k: (0, None)
    search_categories = get_category_parameters = lambda *a, **k: (None, 'Not available')
    build_offer_parameters = build_offer_parameters_ai = extract_parameters_with_ai = lambda *a, **k: []

try:
    from .printer_manager import (
        PrinterManager,
        ProductLabel,
        LabelConfig,
        print_product_label_sync,
        generate_label_preview_sync,
        scan_printers_sync,
        get_printer_manager,
        get_niimprint_status,
        NIIMPRINT_AVAILABLE,
        BLEAK_AVAILABLE,
        IMAGING_AVAILABLE
    )
except ImportError as e:
    print(f"⚠️ printer_manager: {e}")
    PrinterManager = ProductLabel = LabelConfig = None
    print_product_label_sync = generate_label_preview_sync = scan_printers_sync = lambda *a, **k: None
    get_printer_manager = lambda: None
    get_niimprint_status = lambda: {'available': False}
    NIIMPRINT_AVAILABLE = BLEAK_AVAILABLE = IMAGING_AVAILABLE = False

try:
    from .inventory_utils import (
        SmartQuantityParser,
        import_excel_manifest,
        update_stock_on_sale,
        sync_orders_with_stock
    )
except ImportError as e:
    print(f"⚠️ inventory_utils: {e}")
    SmartQuantityParser = None
    import_excel_manifest = update_stock_on_sale = sync_orders_with_stock = lambda *a, **k: None

try:
    from .magazynier_extensions import register_printer_routes
except ImportError as e:
    print(f"⚠️ magazynier_extensions: {e}")
    register_printer_routes = lambda *a, **k: None

__all__ = [
    'init_db', 'get_db', 'get_config', 'set_config',
    'get_amazon_image_url', 'oblicz_cene_allegro', 'generuj_opis_ai',
    'magazynier_bp', 'paletomat_bp', 'telegram_bp', 'allegro_bp',
    'send_telegram', 'bot_status', 'start_bot', 'stop_bot',
    'alert_sprzedaz', 'alert_niski_stan', 'alert_nowa_oferta', 'raport_dzienny',
    'allegro_configured', 'allegro_authenticated', 'allegro_sync',
    'PrinterManager', 'ProductLabel', 'LabelConfig',
    'print_product_label_sync', 'generate_label_preview_sync',
    'scan_printers_sync', 'get_printer_manager',
    'SmartQuantityParser', 'import_excel_manifest',
    'update_stock_on_sale', 'sync_orders_with_stock',
    'register_printer_routes'
]
