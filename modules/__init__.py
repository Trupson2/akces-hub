"""
Akces Hub - Moduły
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
from .magazynier import magazynier_bp, get_stats as mag_stats
from .paletomat import paletomat_bp, get_stats as pal_stats
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

# Nowe moduły v2.7
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
from .inventory_utils import (
    SmartQuantityParser,
    import_excel_manifest,
    update_stock_on_sale,
    sync_orders_with_stock
)
from .magazynier_extensions import register_printer_routes

__all__ = [
    'init_db', 'get_db', 'get_config', 'set_config',
    'get_amazon_image_url', 'oblicz_cene_allegro', 'generuj_opis_ai',
    'magazynier_bp', 'paletomat_bp', 'telegram_bp', 'allegro_bp',
    'send_telegram', 'bot_status', 'start_bot', 'stop_bot',
    'alert_sprzedaz', 'alert_niski_stan', 'alert_nowa_oferta', 'raport_dzienny',
    'allegro_configured', 'allegro_authenticated', 'allegro_sync',
    # Nowe
    'PrinterManager', 'ProductLabel', 'LabelConfig',
    'print_product_label_sync', 'generate_label_preview_sync', 
    'scan_printers_sync', 'get_printer_manager',
    'SmartQuantityParser', 'import_excel_manifest',
    'update_stock_on_sale', 'sync_orders_with_stock',
    'register_printer_routes'
]
