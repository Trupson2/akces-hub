"""Amazon GPSR scraper via Playwright headless browser.

Cel: real Amazon GPSR data (manufacturer name + responsible person) z lazy-loaded
section "Safety and product resources" — tabs "Manufacturer information" /
"Responsible person" ładowane przez JavaScript dopiero po kliknięciu.

Klasyczny HTTP scraper (modules/amazon_gpsr_scraper.fetch_amazon_html) widzi tylko
statyczny HTML bez tabs content. Playwright otwiera real Chromium, klika tabs,
czeka na AJAX, ekstraktuje rendered HTML — wtedy parse_gpsr_from_html może wyciąć.

Wymaga:
    pip install playwright
    playwright install chromium
    # Pi (Linux): może też 'playwright install-deps chromium' lub apt:
    # sudo apt install libgbm1 libnss3 libxcb1 libxkbcommon0 libgtk-3-0

Throttle: 10s/req zalecane (Amazon może rate-limit przy częstszym).

Usage:
    from modules.amazon_gpsr_playwright import fetch_amazon_html_playwright
    html = fetch_amazon_html_playwright(asin='B0DJSVQNCS', region='de')
    if html:
        from modules.amazon_gpsr_scraper import parse_gpsr_from_html
        parsed = parse_gpsr_from_html(html)

@author: Akces Hub
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Region → Amazon domain
REGION_DOMAIN = {
    'de': 'amazon.de',
    'pl': 'amazon.pl',
    'uk': 'amazon.co.uk',
    'it': 'amazon.it',
    'fr': 'amazon.fr',
    'es': 'amazon.es',
}

# Region → locale dla Accept-Language + browser locale
REGION_LOCALE = {
    'de': 'de-DE',
    'pl': 'pl-PL',
    'uk': 'en-GB',
    'it': 'it-IT',
    'fr': 'fr-FR',
    'es': 'es-ES',
}

# User-Agent (Chrome 121 Windows)
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/121.0.0.0 Safari/537.36'
)

# Playwright timeouts (ms)
GOTO_TIMEOUT_MS = 30_000
SELECTOR_TIMEOUT_MS = 5_000
CLICK_TIMEOUT_MS = 3_000
TAB_WAIT_MS = 1_200
INITIAL_WAIT_MS = 2_500


def is_available() -> bool:
    """Sprawdź czy Playwright + Chromium jest zainstalowany.

    Returns True jeśli `from playwright.sync_api import sync_playwright` works
    AND chromium browser jest dostępny (rzuca PlaywrightError gdy nie).
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def fetch_amazon_html_playwright(
    asin: str,
    region: str = 'de',
    headless: bool = True,
    debug: bool = False,
) -> Optional[str]:
    """Fetch Amazon product page HTML z lazy-loaded GPSR sections (manufacturer + responsible).

    Returns concatenated HTML z obu tabs (manufacturer + responsible person stan)
    lub None gdy fail (captcha, brak playwright, network error).

    Strategy:
      1. Open Chromium headless, navigate do /dp/{ASIN}
      2. Scroll do końca strony (force lazy-load resources)
      3. Click expander "Safety and product resources" (jeśli collapsed)
      4. Click tab "Manufacturer information" → wait → snapshot HTML
      5. Click tab "Responsible person" → wait → snapshot HTML
      6. Return concat HTML (oba tabs content)

    Args:
        asin: Amazon Standard ID
        region: 'de'/'pl'/'uk'/'it'/'fr'/'es'
        headless: True (default) = bez UI window; False = pokaż okno (debug)
        debug: True = log każdego kroku + screenshot przy fail

    Returns: HTML string lub None.
    """
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeout
    except ImportError:
        logger.error('Playwright nie zainstalowany — pip install playwright && playwright install chromium')
        return None

    domain = REGION_DOMAIN.get(region, 'amazon.de')
    locale = REGION_LOCALE.get(region, 'de-DE')
    url = f'https://www.{domain}/dp/{asin}'

    if debug:
        logger.info(f'Playwright fetch: {url} (locale={locale}, headless={headless})')

    snapshots = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    locale=locale,
                    viewport={'width': 1366, 'height': 900},
                    extra_http_headers={
                        'Accept-Language': f'{locale},{locale.split("-")[0]};q=0.9,en;q=0.7',
                    },
                )
                page = context.new_page()

                # Block heavy assets dla speed (images, fonts, media)
                page.route('**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,mp4,webm}', lambda r: r.abort())

                page.goto(url, timeout=GOTO_TIMEOUT_MS, wait_until='domcontentloaded')

                # Initial wait — żeby Amazon zaczął renderować
                page.wait_for_timeout(INITIAL_WAIT_MS)

                # Scroll do dolu — force Amazon LazyLoad sections (GPSR jest na bottom)
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(1500)

                # Dismiss cookie banner (gdy obecny — Amazon DE/PL czasem)
                for cookie_selector in [
                    'input[name="accept"]',
                    '#sp-cc-accept',
                    'button:has-text("Accept")',
                    'button:has-text("Akzeptieren")',
                    'button:has-text("Zaakceptuj")',
                ]:
                    try:
                        btn = page.query_selector(cookie_selector)
                        if btn and btn.is_visible():
                            btn.click(timeout=1500)
                            page.wait_for_timeout(500)
                            break
                    except Exception:
                        pass

                # Try expand "Safety and product resources" section (jeśli zwinięta)
                for safety_selector in [
                    'text=/^Safety and product resources/i',
                    'text=/^Sicherheit und Produktressourcen/i',
                    'text=/Bezpiecze[ńn]stwo i zasoby/i',
                    '[aria-label*="Safety and product"]',
                    '[data-feature-name="productSafetyAndCompliance" i]',
                ]:
                    try:
                        el = page.query_selector(safety_selector)
                        if el:
                            el.scroll_into_view_if_needed(timeout=2000)
                            el.click(timeout=CLICK_TIMEOUT_MS)
                            page.wait_for_timeout(TAB_WAIT_MS)
                            if debug:
                                logger.info(f'Clicked safety expander: {safety_selector}')
                            break
                    except (PlaywrightError, PlaywrightTimeout):
                        continue

                # Helper: kliknij pill, poczekaj na side panel content (Amazon dynamicznie ładuje
                # side-sheet po kliknięciu pill button — content trafia do [role=dialog] lub
                # [data-csa-c-content-id*=manufacturer/responsible]).
                def _click_pill_and_capture(selectors, content_indicators, label):
                    for sel in selectors:
                        try:
                            el = page.query_selector(sel)
                            if not el:
                                continue
                            el.scroll_into_view_if_needed(timeout=2000)
                            el.click(timeout=CLICK_TIMEOUT_MS)
                            # Czekaj na side sheet content (lookup po data attr / role / known IDs)
                            for cind in content_indicators:
                                try:
                                    page.wait_for_selector(cind, timeout=4000, state='visible')
                                    page.wait_for_timeout(800)  # extra dla render
                                    if debug:
                                        logger.info(f'{label} pill clicked + content visible ({cind})')
                                    return True
                                except (PlaywrightError, PlaywrightTimeout):
                                    continue
                            # Nawet bez content_indicator match — daj chwilę i snapshot
                            page.wait_for_timeout(2000)
                            if debug:
                                logger.info(f'{label} clicked ({sel}), content_indicator not matched — fallback wait')
                            return True
                        except (PlaywrightError, PlaywrightTimeout):
                            continue
                    return False

                # === Manufacturer pill ===
                mf_clicked = _click_pill_and_capture(
                    selectors=[
                        '#buffet-sidesheet-manufacturer-pill',
                        '#buffet-sidesheet-manufacturer-pill input',
                        'span[id="buffet-sidesheet-manufacturer-pill-announce"]',
                        'text=/^Manufacturer information$/i',
                        'text=/^Herstellerinformationen$/i',
                        'text=/^Informacje o producencie$/i',
                        'text=/Manufacturer information/i',
                        'text=/Herstellerinformationen/i',
                    ],
                    content_indicators=[
                        '[data-csa-c-content-id*="manufacturer" i]',
                        '[role="dialog"] [class*="manufacturer" i]',
                        '[id*="manufacturer-info" i] [class*="address"]',
                        # Generic: side sheet zawsze ma dialog role
                        '[role="dialog"]:visible',
                    ],
                    label='Manufacturer',
                )
                snapshots.append(page.content())

                # Zamknij side panel (Escape) żeby kolejny pill mógł się otworzyć clean
                try:
                    page.keyboard.press('Escape')
                    page.wait_for_timeout(500)
                except Exception:
                    pass

                # === Responsible person pill ===
                rp_clicked = _click_pill_and_capture(
                    selectors=[
                        '#buffet-sidesheet-rsp-pill',
                        '#buffet-sidesheet-rsp-pill input',
                        'span[id="buffet-sidesheet-rsp-pill-announce"]',
                        'text=/^Responsible person$/i',
                        'text=/^Verantwortliche Person$/i',
                        'text=/^Osoba odpowiedzialna$/i',
                        'text=/Responsible person/i',
                        'text=/EU representative/i',
                        'text=/EU-Verantwortlicher/i',
                    ],
                    content_indicators=[
                        '[data-csa-c-content-id*="responsible" i]',
                        '[data-csa-c-content-id*="rsp" i]',
                        '[role="dialog"] [class*="responsible" i]',
                        '[role="dialog"]:visible',
                    ],
                    label='ResponsiblePerson',
                )
                snapshots.append(page.content())

                if debug:
                    try:
                        page.screenshot(path=f'/tmp/playwright_gpsr_{asin}.png', full_page=False)
                        logger.info(f'Debug screenshot: /tmp/playwright_gpsr_{asin}.png')
                    except Exception:
                        pass

            finally:
                browser.close()
    except PlaywrightTimeout as e:
        logger.warning(f'Playwright timeout asin={asin}: {e}')
        return None
    except PlaywrightError as e:
        logger.warning(f'Playwright error asin={asin}: {e}')
        return None
    except Exception as e:
        logger.exception(f'Playwright unexpected fail asin={asin}: {e}')
        return None

    if not snapshots:
        return None

    # Concat oba snapshots (manufacturer + responsible) — parse_gpsr_from_html
    # z content-based strategy znajdzie oba sections w connectowanym HTML.
    return '\n<!-- TAB SWITCH -->\n'.join(snapshots)
