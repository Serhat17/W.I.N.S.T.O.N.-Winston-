"""
Web Scraping Utilities — shared helpers for price extraction and web search.

Used by PriceMonitorSkill and TravelSkill for scraping product/flight prices
from the web when dedicated APIs (Amadeus, etc.) are not configured.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

from winston.security.ssrf_guard import validate_url, SSRFError

logger = logging.getLogger("winston.utils.scraper")

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
    GET a page and return its HTML text.
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
        ".price",
        ".product-price",
        ".current-price",
        ".offer-price",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        ".a-price .a-offscreen",     # Amazon
        "[data-price]",
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
    Search for flight prices via DuckDuckGo web search.
    Returns a list of ScrapedPrice with flight info.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("ddgs not installed — cannot search flight prices")
            return []

    query = f"flights {origin} to {destination} {date} price"
    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        logger.error(f"DuckDuckGo flight search error: {e}")
        return []

    prices: list[ScrapedPrice] = []
    for r in results:
        body = r.get("body", "")
        title = r.get("title", "")
        href = r.get("href", "")

        text_prices = extract_prices_from_text(f"{title} {body}")
        for p in text_prices:
            p.source = href
            p.description = f"{origin} -> {destination} ({date}): {title}"
            prices.append(p)

    prices.sort(key=lambda p: p.amount)
    return prices[:max_results]


def search_hotel_prices(destination: str, checkin: str = "",
                        max_results: int = 5) -> list[ScrapedPrice]:
    """
    Search for hotel prices via DuckDuckGo web search.
    Returns a list of ScrapedPrice with hotel info.
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
    query = f"hotels {destination}{date_part} price per night"
    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        logger.error(f"DuckDuckGo hotel search error: {e}")
        return []

    prices: list[ScrapedPrice] = []
    for r in results:
        body = r.get("body", "")
        title = r.get("title", "")
        href = r.get("href", "")

        text_prices = extract_prices_from_text(f"{title} {body}")
        for p in text_prices:
            p.source = href
            p.description = f"Hotel in {destination}: {title}"
            prices.append(p)

    prices.sort(key=lambda p: p.amount)
    return prices[:max_results]
