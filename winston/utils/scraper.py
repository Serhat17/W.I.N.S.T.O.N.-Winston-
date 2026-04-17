"""
Web Scraping Utilities — shared helpers for price extraction and web search.

Used by PriceMonitorSkill and TravelSkill for scraping product/flight prices
from the web when dedicated APIs (Amadeus, etc.) are not configured.

Supports three fetching strategies (tried in order):
1. requests + BeautifulSoup (fast, works for static pages with structured data)
2. Playwright headless browser (handles JavaScript-rendered prices)
3. DuckDuckGo web search fallback (finds prices via search results)
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

from winston.security.ssrf_guard import validate_url, SSRFError

logger = logging.getLogger("winston.utils.scraper")

# ── Playwright browser singleton ─────────────────────────────────
_pw_context = None  # reuse across calls to avoid cold-start overhead


def _get_pw_page():
    """
    Return a Playwright page from a reusable browser context.
    Launches Chromium headless on first call.
    """
    global _pw_context
    if _pw_context and _pw_context.get("browser"):
        try:
            # Check if browser is still alive
            page = _pw_context["context"].new_page()
            return page
        except Exception:
            _pw_context = None

    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="de-DE",
            viewport={"width": 1280, "height": 800},
        )
        _pw_context = {"pw": pw, "browser": browser, "context": context}
        return context.new_page()
    except Exception as e:
        logger.warning(f"Playwright not available: {e}")
        return None

# Browser-like headers to avoid bot detection
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}

# Common currency symbols and codes
CURRENCY_SYMBOLS = {
    "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY",
    "₹": "INR", "₺": "TRY", "zł": "PLN", "kr": "SEK",
    "Fr": "CHF", "R$": "BRL", "A$": "AUD", "C$": "CAD",
}

# Regex for prices like $199.99, 199,99 EUR, EUR 199.99, €199, etc.
_PRICE_PATTERNS = [
    # Symbol before amount: $199.99, €1.299,00
    re.compile(
        r"(?P<sym>[€$£¥₹₺])[\s]?"
        r"(?P<amt>[\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)"
    ),
    # Amount then currency code: 199.99 EUR, 1.299,00 USD
    re.compile(
        r"(?P<amt>[\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)"
        r"\s*(?P<cur>[A-Z]{3})\b"
    ),
    # Currency code then amount: EUR 199.99
    re.compile(
        r"(?P<cur>[A-Z]{3})\s+"
        r"(?P<amt>[\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)"
    ),
]

# Known currency codes (subset of ISO 4217)
_VALID_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CNY", "INR", "TRY", "PLN",
    "SEK", "NOK", "DKK", "CHF", "BRL", "AUD", "CAD", "MXN",
    "KRW", "RUB", "ZAR", "NZD", "HKD", "SGD", "TWD", "THB",
}


@dataclass
class ScrapedPrice:
    """A single price found by scraping."""
    amount: float
    currency: str
    source: str = ""          # URL or search result title
    description: str = ""     # Product name, flight route, etc.
    raw_text: str = ""        # Original text the price was parsed from
    metadata: dict = field(default_factory=dict)


def _normalize_amount(raw: str) -> float:
    """
    Parse a price string like '1.299,00' or '1,299.00' into a float.

    Heuristic: if the last separator is a comma followed by exactly 2 digits,
    treat comma as decimal separator (European format: 1.299,00 -> 1299.00).
    Otherwise treat dot as decimal separator (US format: 1,299.00 -> 1299.00).
    """
    raw = raw.strip()
    if not raw:
        return 0.0

    # European: last separator is comma + 2 digits
    if re.search(r",\d{2}$", raw):
        # 1.299,00 -> 1299.00
        return float(raw.replace(".", "").replace(",", "."))

    # US: last separator is dot + 2 digits
    if re.search(r"\.\d{2}$", raw):
        # 1,299.00 -> 1299.00
        return float(raw.replace(",", ""))

    # No decimal part — strip all separators
    return float(re.sub(r"[.,]", "", raw))


def fetch_page(url: str, timeout: int = 10) -> Optional[str]:
    """
    GET a page and return its HTML text (requests only — fast).
    Returns None on any error (timeout, HTTP error, SSRF block, etc.).
    """
    try:
        validate_url(url)
    except SSRFError as e:
        logger.warning(f"SSRF blocked in fetch_page: {e}")
        return None

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None


def fetch_page_browser(url: str, timeout: int = 15000) -> Optional[str]:
    """
    Fetch a page using Playwright (headless Chromium).
    Handles JavaScript-rendered content that requests can't see.
    Returns full page HTML or None.
    """
    try:
        validate_url(url)
    except SSRFError as e:
        logger.warning(f"SSRF blocked in fetch_page_browser: {e}")
        return None

    page = _get_pw_page()
    if not page:
        return None

    try:
        page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        # Wait for price elements to render (JS frameworks need time)
        page.wait_for_timeout(2000)
        # Try to dismiss cookie banners that might overlay content
        _dismiss_cookie_banner(page)
        html = page.content()
        return html
    except Exception as e:
        logger.warning(f"Playwright fetch failed for {url}: {e}")
        return None
    finally:
        try:
            page.close()
        except Exception:
            pass


def _dismiss_cookie_banner(page):
    """Try to dismiss cookie consent banners."""
    selectors = [
        "button:has-text('Accept')", "button:has-text('Akzeptieren')",
        "button:has-text('Alle akzeptieren')", "button:has-text('Accept All')",
        "button:has-text('Alle annehmen')", "button:has-text('OK')",
        "[id*='cookie'] button", "[class*='cookie'] button",
        "[id*='consent'] button", "[class*='consent'] button",
    ]
    for selector in selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=1000)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def search_product_price(product_name: str, store_hint: str = "") -> Optional[ScrapedPrice]:
    """
    Search for a product price using multiple strategies:
    1. Geizhals.de price comparison (most reliable for DE/AT products)
    2. DuckDuckGo text search fallback
    """
    # Strategy 1: Geizhals.de — German price comparison, works with Playwright
    result = _search_geizhals(product_name)
    if result:
        return result

    # Strategy 2: DuckDuckGo text search
    result = _search_ddg_prices(product_name, store_hint)
    if result:
        return result

    return None


def _search_geizhals(product_name: str) -> Optional[ScrapedPrice]:
    """
    Search geizhals.de for a product price.
    Geizhals is a German/Austrian price comparison site that:
    - Works reliably with Playwright
    - Returns structured JSON-LD data with real prices
    - Covers electronics, appliances, and more
    """
    import urllib.parse
    encoded = urllib.parse.quote_plus(product_name)
    url = f"https://geizhals.de/?fs={encoded}&hloc=at&hloc=de"
    logger.info(f"Searching geizhals.de: {product_name}")

    html = fetch_page_browser(url, timeout=15000)
    if not html:
        # Try without Playwright (static HTML)
        html = fetch_page(url)

    if not html:
        return None

    # Try structured data extraction first
    price = extract_product_price(html, url=url)
    if price and price.amount > 0:
        price.description = product_name
        logger.info(f"Geizhals price (structured): {price.amount} {price.currency}")
        return price

    # Fallback: extract prices from visible text
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)[:3000]

    # Look for "ab € XXX" or "ab XXX €" patterns (geizhals format)
    ab_pattern = re.compile(r'ab\s+[€]\s*([\d.,]+)|ab\s+([\d.,]+)\s*€', re.IGNORECASE)
    for m in ab_pattern.finditer(text):
        raw = m.group(1) or m.group(2)
        try:
            amount = _normalize_amount(raw)
            if amount > 1.0:
                logger.info(f"Geizhals price (ab-pattern): {amount} EUR")
                return ScrapedPrice(
                    amount=amount, currency="EUR",
                    source=url, description=product_name,
                    raw_text=m.group(0),
                )
        except (ValueError, TypeError):
            continue

    # General price extraction from page text
    prices = extract_prices_from_text(text)
    # Filter to EUR prices > 5€ (skip tiny amounts)
    eur_prices = [p for p in prices if p.currency == "EUR" and p.amount > 5]
    if eur_prices:
        # Take the lowest EUR price (typically the best deal)
        best = min(eur_prices, key=lambda p: p.amount)
        best.source = url
        best.description = product_name
        logger.info(f"Geizhals price (text): {best.amount} {best.currency}")
        return best

    return None


def _search_ddg_prices(product_name: str, store_hint: str = "") -> Optional[ScrapedPrice]:
    """Search DuckDuckGo for product prices in text snippets."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("ddgs not installed — cannot search product prices")
            return None

    query = f"{product_name} Preis EUR kaufen" + (f" {store_hint}" if store_hint else "")
    logger.info(f"Searching DDG: {query}")

    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=8))
    except Exception as e:
        logger.error(f"DuckDuckGo product search error: {e}")
        return None

    best_price = None
    for r in results:
        body = r.get("body", "")
        title = r.get("title", "")
        href = r.get("href", "")
        text = f"{title} {body}"

        prices = extract_prices_from_text(text)
        for p in prices:
            if p.amount < 5.0 or p.amount > 50000:
                continue
            p.source = href
            p.description = title
            if best_price is None or p.amount < best_price.amount:
                best_price = p

    return best_price


def extract_product_price(html: str, url: str = "") -> Optional[ScrapedPrice]:
    """
    Try to extract a product price from HTML using multiple strategies:
    1. JSON-LD structured data (schema.org/Product)
    2. OpenGraph meta tags (product:price:amount)
    3. Microdata (itemprop="price")
    4. Common CSS class patterns (price, product-price, etc.)
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1: JSON-LD
    price = _extract_jsonld_price(soup)
    if price:
        price.source = url
        return price

    # Strategy 2: OpenGraph
    price = _extract_og_price(soup)
    if price:
        price.source = url
        return price

    # Strategy 3: Microdata
    price = _extract_microdata_price(soup)
    if price:
        price.source = url
        return price

    # Strategy 4: CSS class patterns
    price = _extract_css_price(soup)
    if price:
        price.source = url
        return price

    return None


def _extract_jsonld_price(soup: BeautifulSoup) -> Optional[ScrapedPrice]:
    """Extract price from JSON-LD script tags (schema.org Product/Offer)."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        # Handle @graph arrays
        items = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and "@graph" in data:
            items = data["@graph"]

        for item in items:
            if not isinstance(item, dict):
                continue

            offer = None
            name = item.get("name", "")

            if item.get("@type") == "Product":
                offers = item.get("offers", {})
                if isinstance(offers, list) and offers:
                    offer = offers[0]
                elif isinstance(offers, dict):
                    offer = offers
            elif item.get("@type") in ("Offer", "AggregateOffer"):
                offer = item

            if offer and ("price" in offer or "lowPrice" in offer):
                raw_price = offer.get("price") or offer.get("lowPrice", "")
                currency = offer.get("priceCurrency", "EUR")
                try:
                    amount = float(str(raw_price).replace(",", "."))
                    return ScrapedPrice(
                        amount=amount,
                        currency=currency,
                        description=name,
                        raw_text=f"{amount} {currency}",
                    )
                except (ValueError, TypeError):
                    continue
    return None


def _extract_og_price(soup: BeautifulSoup) -> Optional[ScrapedPrice]:
    """Extract price from OpenGraph product meta tags."""
    og_amount = soup.find("meta", property="product:price:amount")
    og_currency = soup.find("meta", property="product:price:currency")
    og_title = soup.find("meta", property="og:title")

    if og_amount and og_amount.get("content"):
        try:
            amount = float(og_amount["content"].replace(",", "."))
            currency = og_currency["content"] if og_currency else "EUR"
            name = og_title["content"] if og_title else ""
            return ScrapedPrice(
                amount=amount,
                currency=currency,
                description=name,
                raw_text=f"{amount} {currency}",
            )
        except (ValueError, TypeError):
            pass
    return None


def _extract_microdata_price(soup: BeautifulSoup) -> Optional[ScrapedPrice]:
    """Extract price from microdata (itemprop='price')."""
    price_el = soup.find(attrs={"itemprop": "price"})
    if price_el:
        raw = price_el.get("content") or price_el.get_text(strip=True)
        currency_el = soup.find(attrs={"itemprop": "priceCurrency"})
        currency = (
            (currency_el.get("content") or currency_el.get_text(strip=True))
            if currency_el
            else "EUR"
        )
        name_el = soup.find(attrs={"itemprop": "name"})
        name = (name_el.get("content") or name_el.get_text(strip=True)) if name_el else ""

        try:
            amount = _normalize_amount(raw)
            if amount > 0:
                return ScrapedPrice(
                    amount=amount,
                    currency=currency,
                    description=name,
                    raw_text=raw,
                )
        except (ValueError, TypeError):
            pass
    return None


def _extract_css_price(soup: BeautifulSoup) -> Optional[ScrapedPrice]:
    """Extract price from common CSS class/id patterns."""
    selectors = [
        # Generic
        ".price", ".product-price", ".current-price", ".offer-price",
        ".price-current", ".sale-price", ".final-price", ".regular-price",
        "[data-price]", "[data-product-price]",
        # Amazon
        "#priceblock_ourprice", "#priceblock_dealprice",
        ".a-price .a-offscreen", ".a-price-whole",
        "#corePrice_feature_div .a-offscreen",
        "#apex_offerDisplay_desktop .a-offscreen",
        "span.a-price span.a-offscreen",
        # eBay
        ".x-price-primary span", ".x-bin-price__content span",
        "#prcIsum", ".vi-price",
        # MediaMarkt / Saturn (DE)
        "[data-test='product-price']", ".price--product",
        "[class*='StyledPart']", "[class*='price-value']",
        # Otto, Zalando, etc. (DE)
        "[class*='productPrice']", "[class*='offer-price']",
        "[data-testid*='price']", "[class*='Price__amount']",
        # Generic fallbacks
        "[class*='priceAmount']", "[class*='price-amount']",
        "[class*='product__price']", "[class*='pdp-price']",
        "ins .amount", ".woocommerce-Price-amount",
    ]
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            # data-price attribute is a clean float
            data_price = el.get("data-price")
            if data_price:
                try:
                    return ScrapedPrice(
                        amount=float(data_price),
                        currency="EUR",
                        raw_text=data_price,
                    )
                except ValueError:
                    pass

            text = el.get_text(strip=True)
            prices = extract_prices_from_text(text)
            if prices:
                return prices[0]
    return None


def extract_prices_from_text(text: str) -> list[ScrapedPrice]:
    """
    Extract all prices from a block of text using regex.
    Returns a list of ScrapedPrice, sorted by amount ascending.
    """
    found: list[ScrapedPrice] = []
    seen: set[tuple[float, str]] = set()

    for pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groupdict()
            raw_amt = groups.get("amt", "")

            sym = groups.get("sym", "")
            cur = groups.get("cur", "")
            currency = CURRENCY_SYMBOLS.get(sym, cur) or "EUR"

            # Skip invalid currency codes
            if cur and cur not in _VALID_CURRENCIES:
                continue

            try:
                amount = _normalize_amount(raw_amt)
                if amount <= 0:
                    continue
            except (ValueError, TypeError):
                continue

            key = (amount, currency)
            if key not in seen:
                seen.add(key)
                found.append(ScrapedPrice(
                    amount=amount,
                    currency=currency,
                    raw_text=match.group(0),
                ))

    found.sort(key=lambda p: p.amount)
    return found


def search_flight_prices(origin: str, destination: str, date: str,
                         max_results: int = 5) -> list[ScrapedPrice]:
    """
    Search for flight prices using multiple strategies:
    1. SerpAPI Google Flights (best — real prices + booking links)
    2. Kayak.de route prices via Playwright
    3. DuckDuckGo text search fallback
    Always attaches booking links in metadata.
    """
    booking_links = get_flight_booking_links(origin, destination, date)

    # Strategy 1: SerpAPI Google Flights (if key configured)
    results = _search_serpapi_flights(origin, destination, date, max_results)
    if results:
        for r in results:
            r.metadata["booking_links"] = booking_links
        return results

    # Strategy 2: Kayak.de route-prices via Playwright
    results = _search_kayak_flights(origin, destination, date)
    if results:
        for r in results[:max_results]:
            r.metadata["booking_links"] = booking_links
        return results[:max_results]

    # Strategy 3: DuckDuckGo text search
    results = _search_ddg_flights(origin, destination, date, max_results)
    if results:
        for r in results:
            r.metadata["booking_links"] = booking_links
        return results

    # No prices found — return a placeholder with booking links
    return [ScrapedPrice(
        amount=0.0,
        currency="EUR",
        source=booking_links[0]["url"],
        description=(
            f"✈️ {origin} → {destination} am {date} — "
            f"keine Preise gefunden. Nutze die Buchungslinks:"
        ),
        metadata={"booking_links": booking_links},
    )]


# ── SerpAPI Google Flights ────────────────────────────────────────

def _search_serpapi_flights(origin: str, destination: str, date: str,
                            max_results: int = 5) -> list[ScrapedPrice]:
    """
    Use SerpAPI to query Google Flights.
    Requires SERPAPI_KEY in environment or Winston settings.
    Free tier: 100 searches/month at serpapi.com
    Returns rich data: airline, times, stops, price, booking link.
    """
    api_key = os.environ.get("SERPAPI_KEY", "")
    if not api_key:
        # Try loading from Winston settings
        try:
            from winston.config import load_settings
            settings = load_settings()
            api_key = getattr(settings.serpapi, "api_key", "")
        except Exception:
            pass
    if not api_key:
        return []

    try:
        from serpapi import GoogleSearch
    except ImportError:
        logger.warning("serpapi not installed — pip install google-search-results")
        return []

    logger.info(f"SerpAPI Google Flights: {origin} → {destination} {date}")
    try:
        params = {
            "engine": "google_flights",
            "departure_id": origin.upper(),
            "arrival_id": destination.upper(),
            "outbound_date": date,
            "currency": "EUR",
            "hl": "de",
            "type": "2",  # one-way
            "api_key": api_key,
        }
        search = GoogleSearch(params)
        data = search.get_dict()
    except Exception as e:
        logger.error(f"SerpAPI error: {e}")
        return []

    results: list[ScrapedPrice] = []

    # Parse best_flights and other_flights
    for category in ["best_flights", "other_flights"]:
        for flight_group in data.get(category, []):
            price = flight_group.get("price")
            if not price or price <= 0:
                continue

            # Build description from flight legs
            legs = flight_group.get("flights", [])
            airlines = set()
            times = []
            stops = len(legs) - 1
            booking_token = flight_group.get("booking_token", "")

            for leg in legs:
                airline = leg.get("airline", "")
                if airline:
                    airlines.add(airline)
                dep_airport = leg.get("departure_airport", {})
                arr_airport = leg.get("arrival_airport", {})
                dep_time = dep_airport.get("time", "")
                arr_time = arr_airport.get("time", "")
                dep_code = dep_airport.get("id", "")
                arr_code = arr_airport.get("id", "")
                flight_no = leg.get("flight_number", "")
                times.append(f"{dep_code} {dep_time} → {arr_code} {arr_time} ({flight_no})")

            duration = flight_group.get("total_duration", 0)
            dur_h = duration // 60
            dur_m = duration % 60

            airline_str = ", ".join(sorted(airlines))
            stops_str = "Direkt" if stops == 0 else f"{stops} Stopp{'s' if stops > 1 else ''}"
            desc = (
                f"✈️ {airline_str} | {stops_str} | {dur_h}h {dur_m}m\n"
                f"   {' → '.join(times)}"
            )

            # Build booking link
            booking_url = ""
            if booking_token:
                booking_url = (
                    f"https://www.google.com/travel/flights/booking?"
                    f"tfs={booking_token}&hl=de&curr=EUR"
                )

            results.append(ScrapedPrice(
                amount=float(price),
                currency="EUR",
                source=booking_url or f"https://www.google.com/travel/flights?q=Flights+from+{origin}+to+{destination}+on+{date}",
                description=desc,
                metadata={
                    "airline": airline_str,
                    "stops": stops,
                    "duration_min": duration,
                    "booking_url": booking_url,
                    "is_best": category == "best_flights",
                },
            ))

            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

    results.sort(key=lambda p: p.amount)
    return results


# ── Kayak.de Flight Prices ────────────────────────────────────────
def _search_kayak_flights(origin: str, destination: str,
                          date: str) -> list[ScrapedPrice]:
    """
    Search kayak.de for flight prices via Playwright.
    Kayak shows route-specific prices like "Flug Düsseldorf – Istanbul 95 €+"
    and explore deal prices in .esgW-price elements.
    """
    # IATA → German city name mapping for text matching
    _iata_city: dict[str, str] = {
        "DUS": "Düsseldorf", "FRA": "Frankfurt", "MUC": "München",
        "BER": "Berlin", "HAM": "Hamburg", "CGN": "Köln",
        "STR": "Stuttgart", "HAJ": "Hannover", "DTM": "Dortmund",
        "BRE": "Bremen", "NUE": "Nürnberg", "LEJ": "Leipzig",
        "DRS": "Dresden", "FMO": "Münster", "PAD": "Paderborn",
        "IST": "Istanbul", "SAW": "Sabiha", "AYT": "Antalya",
        "ADB": "Izmir", "ESB": "Ankara", "DLM": "Dalaman",
        "BJV": "Bodrum", "TZX": "Trabzon",
        "BCN": "Barcelona", "PMI": "Palma", "AGP": "Málaga",
        "ATH": "Athen", "SKG": "Thessaloniki", "HER": "Heraklion",
        "FCO": "Rom", "MXP": "Mailand", "VCE": "Venedig",
        "CDG": "Paris", "LHR": "London", "AMS": "Amsterdam",
        "VIE": "Wien", "ZRH": "Zürich", "PRG": "Prag",
        "BUD": "Budapest", "WAW": "Warschau", "OTP": "Bukarest",
        "SOF": "Sofia", "LIS": "Lissabon", "DUB": "Dublin",
        "CPH": "Kopenhagen", "ARN": "Stockholm", "OSL": "Oslo",
        "HEL": "Helsinki", "BKK": "Bangkok", "JFK": "New York",
        "LAX": "Los Angeles", "DXB": "Dubai", "DOH": "Doha",
    }

    kayak_url = (
        f"https://www.kayak.de/flights/"
        f"{origin.upper()}-{destination.upper()}/{date}"
        f"?sort=price_a"
    )
    logger.info(f"Searching Kayak: {origin} → {destination} on {date}")

    html = fetch_page_browser(kayak_url, timeout=20000)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    route_prices: list[ScrapedPrice] = []
    explore_prices: list[ScrapedPrice] = []
    seen: set[float] = set()

    origin_city = _iata_city.get(origin.upper(), origin)
    dest_city = _iata_city.get(destination.upper(), destination)

    # 1) Route-specific prices: "Flug {origin_city} – {dest_city} XX €"
    #    These are the REAL prices for the exact route
    route_pat = re.compile(
        rf'Flug\s+{re.escape(origin_city)}[\s\u2013\u2014–-]+{re.escape(dest_city)}'
        rf'\s+(\d{{2,5}})\s*€',
        re.IGNORECASE,
    )
    for m in route_pat.finditer(text):
        amount = float(m.group(1))
        if 20 <= amount <= 10000 and amount not in seen:
            seen.add(amount)
            route_prices.append(ScrapedPrice(
                amount=amount,
                currency="EUR",
                source=kayak_url,
                description=f"✈️ {origin_city} → {dest_city} ab {amount:.0f}€ (Kayak)",
                raw_text=m.group(0),
            ))

    # If we found route-specific prices, return those (most reliable)
    if route_prices:
        route_prices.sort(key=lambda p: p.amount)
        return route_prices

    # 2) esgW-price elements with destination context
    #    On explore pages these show deals from the origin city
    for el in soup.select(".esgW-price"):
        price_text = el.get_text(strip=True)
        ab_match = re.search(r'(\d+)', price_text)
        if not ab_match:
            continue
        amount = float(ab_match.group(1))
        if amount < 20 or amount > 10000 or amount in seen:
            continue
        seen.add(amount)
        # Walk up to find the deal context (city name, duration)
        parent = el.find_parent()
        grandparent = parent.find_parent() if parent else None
        ctx = (grandparent or parent or el).get_text(" ", strip=True)[:80] if parent else ""
        # Check if this deal mentions the destination city
        is_route = dest_city.lower() in ctx.lower()
        explore_prices.append(ScrapedPrice(
            amount=amount,
            currency="EUR",
            source=kayak_url,
            description=(
                f"✈️ {origin_city} → {dest_city} ab {amount:.0f}€"
                if is_route
                else f"✈️ ab {origin_city}: {ctx.strip()}"
            ),
            raw_text=price_text,
        ))

    # 3) Also look for "Flug {any city} – {dest_city} XX €" patterns
    #    to find alternative route prices from other origins
    any_route_pat = re.compile(
        rf'Flug\s+(\w+)[\s\u2013\u2014–-]+{re.escape(dest_city)}'
        rf'\s+(\d{{2,5}})\s*€',
        re.IGNORECASE,
    )
    for m in any_route_pat.finditer(text):
        city = m.group(1)
        amount = float(m.group(2))
        if 20 <= amount <= 10000 and amount not in seen:
            seen.add(amount)
            explore_prices.append(ScrapedPrice(
                amount=amount,
                currency="EUR",
                source=kayak_url,
                description=f"✈️ {city} → {dest_city} ab {amount:.0f}€",
                raw_text=m.group(0),
            ))

    if explore_prices:
        explore_prices.sort(key=lambda p: p.amount)
        return explore_prices

    # 4) Last resort: any EUR prices in flight range from page text
    fallback: list[ScrapedPrice] = []
    for m in re.finditer(r'(\d{2,4})\s*€', text):
        amount = float(m.group(1))
        if 30 <= amount <= 5000 and amount not in seen:
            seen.add(amount)
            fallback.append(ScrapedPrice(
                amount=amount,
                currency="EUR",
                source=kayak_url,
                description=f"✈️ {origin_city} → {dest_city} ({date})",
                raw_text=m.group(0),
            ))
            if len(fallback) >= 10:
                break

    fallback.sort(key=lambda p: p.amount)
    return fallback


def _search_ddg_flights(origin: str, destination: str, date: str,
                        max_results: int = 5) -> list[ScrapedPrice]:
    """Search DuckDuckGo for flight prices in text snippets."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("ddgs not installed — cannot search flight prices")
            return []

    queries = [
        f"Flug {origin} nach {destination} {date} ab Preis",
        f"flight {origin} to {destination} {date} price from",
        f"{origin} {destination} {date} cheapest flight EUR",
    ]

    all_prices: list[ScrapedPrice] = []
    seen_amounts: set[float] = set()

    ddgs = DDGS()
    for query in queries:
        try:
            results = list(ddgs.text(query, max_results=max_results))
        except Exception as e:
            logger.error(f"DuckDuckGo flight search error: {e}")
            continue

        for r in results:
            body = r.get("body", "")
            title = r.get("title", "")
            href = r.get("href", "")

            text_prices = extract_prices_from_text(f"{title} {body}")
            for p in text_prices:
                if p.amount < 20 or p.amount > 15000:
                    continue
                if p.amount in seen_amounts:
                    continue
                seen_amounts.add(p.amount)
                p.source = href
                p.description = f"{origin} → {destination} ({date}): {title}"
                all_prices.append(p)

        if all_prices:
            break

    all_prices.sort(key=lambda p: p.amount)
    return all_prices[:max_results]


def get_flight_booking_links(origin: str, destination: str,
                             date: str) -> list[dict[str, str]]:
    """
    Generate direct booking/search links for a flight route.
    Uses fast_flights to build a proper Google Flights deep-link.
    Returns a list of {name, url} dicts.
    """
    o, d = origin.upper(), destination.upper()

    # Build a proper Google Flights deep-link with TFS parameter
    gf_url = f"https://www.google.com/travel/flights?q=Flights+from+{o}+to+{d}+on+{date}&hl=de&curr=EUR"
    try:
        from fast_flights import FlightData, Passengers
        from fast_flights.flights_impl import TFSData
        tfs = TFSData.from_interface(
            flight_data=[FlightData(date=date, from_airport=o, to_airport=d)],
            trip="one-way",
            passengers=Passengers(adults=1),
            seat="economy",
        )
        b64 = tfs.as_b64().decode()
        gf_url = f"https://www.google.com/travel/flights/search?tfs={b64}&hl=de&curr=EUR"
    except Exception:
        pass  # fallback to simple URL

    return [
        {
            "name": "Google Flights",
            "url": gf_url,
        },
        {
            "name": "Kayak",
            "url": f"https://www.kayak.de/flights/{o}-{d}/{date}?sort=price_a",
        },
        {
            "name": "Skyscanner",
            "url": f"https://www.skyscanner.de/transport/fluge/{o.lower()}/{d.lower()}/{date.replace('-', '')}/?adultsv2=1&cabinclass=economy",
        },
        {
            "name": "Booking.com Flights",
            "url": f"https://flights.booking.com/flights/{o}-{d}/?type=ONEWAY&adults=1&cabinClass=ECONOMY&depart={date}",
        },
        {
            "name": "Momondo",
            "url": f"https://www.momondo.de/flights/{o}-{d}/{date}?sort=price_a",
        },
    ]


def search_hotel_prices(destination: str, checkin: str = "",
                        max_results: int = 5) -> list[ScrapedPrice]:
    """
    Search for hotel prices via DuckDuckGo web search.
    Uses multiple query strategies for better results.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("ddgs not installed — cannot search hotel prices")
            return []

    date_part = f" {checkin}" if checkin else ""
    queries = [
        f"Hotel {destination}{date_part} Preis pro Nacht",
        f"hotel {destination}{date_part} price per night EUR",
        f"günstige Hotels {destination}{date_part}",
    ]

    all_prices: list[ScrapedPrice] = []
    seen_amounts: set[float] = set()

    ddgs = DDGS()
    for query in queries:
        try:
            results = list(ddgs.text(query, max_results=max_results))
        except Exception as e:
            logger.error(f"DuckDuckGo hotel search error: {e}")
            continue

        for r in results:
            body = r.get("body", "")
            title = r.get("title", "")
            href = r.get("href", "")

            text_prices = extract_prices_from_text(f"{title} {body}")
            for p in text_prices:
                # Filter out unreasonable hotel prices
                if p.amount < 15 or p.amount > 5000:
                    continue
                if p.amount in seen_amounts:
                    continue
                seen_amounts.add(p.amount)
                p.source = href
                p.description = f"Hotel in {destination}: {title}"
                all_prices.append(p)

        if all_prices:
            break

    all_prices.sort(key=lambda p: p.amount)
    return all_prices[:max_results]
