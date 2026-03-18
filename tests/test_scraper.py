"""
Tests for winston.utils.scraper — price extraction from HTML and text.
"""

import pytest
from winston.utils.scraper import (
    ScrapedPrice,
    extract_prices_from_text,
    extract_product_price,
    fetch_page,
    search_flight_prices,
    _normalize_amount,
    _extract_jsonld_price,
    _extract_og_price,
    _extract_microdata_price,
)
from bs4 import BeautifulSoup


# ── _normalize_amount ──


class TestNormalizeAmount:
    def test_us_format(self):
        assert _normalize_amount("1,299.00") == 1299.00

    def test_european_format(self):
        assert _normalize_amount("1.299,00") == 1299.00

    def test_simple_integer(self):
        assert _normalize_amount("199") == 199.0

    def test_simple_decimal(self):
        assert _normalize_amount("199.99") == 199.99

    def test_european_decimal(self):
        assert _normalize_amount("199,99") == 199.99

    def test_empty_string(self):
        assert _normalize_amount("") == 0.0

    def test_large_us(self):
        assert _normalize_amount("12,500.00") == 12500.00

    def test_large_european(self):
        assert _normalize_amount("12.500,00") == 12500.00


# ── extract_prices_from_text ──


class TestExtractPricesFromText:
    def test_dollar_sign(self):
        prices = extract_prices_from_text("The flight costs $199.99")
        assert len(prices) >= 1
        assert prices[0].amount == 199.99
        assert prices[0].currency == "USD"

    def test_euro_sign(self):
        prices = extract_prices_from_text("Preis: €1.299,00")
        assert len(prices) >= 1
        assert prices[0].amount == 1299.00
        assert prices[0].currency == "EUR"

    def test_currency_code_after(self):
        prices = extract_prices_from_text("Flight for 299.00 EUR")
        assert len(prices) >= 1
        assert prices[0].amount == 299.00
        assert prices[0].currency == "EUR"

    def test_currency_code_before(self):
        prices = extract_prices_from_text("Starting from USD 450")
        assert len(prices) >= 1
        assert prices[0].amount == 450.0
        assert prices[0].currency == "USD"

    def test_gbp(self):
        prices = extract_prices_from_text("Price: £89.99 per night")
        assert len(prices) >= 1
        assert prices[0].amount == 89.99
        assert prices[0].currency == "GBP"

    def test_multiple_prices_sorted(self):
        prices = extract_prices_from_text("From $500 to $200 to $800")
        assert len(prices) >= 2
        # Should be sorted ascending
        assert prices[0].amount <= prices[-1].amount

    def test_no_prices(self):
        prices = extract_prices_from_text("No prices mentioned here")
        assert len(prices) == 0

    def test_invalid_currency_code_skipped(self):
        # "THE" is not a valid currency code
        prices = extract_prices_from_text("THE 100 best things")
        assert all(p.currency != "THE" for p in prices)

    def test_turkish_lira(self):
        prices = extract_prices_from_text("Fiyat: ₺2.500,00")
        assert len(prices) >= 1
        assert prices[0].currency == "TRY"


# ── JSON-LD extraction ──


class TestJsonLdExtraction:
    def test_product_with_offers(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "iPhone 16 Pro",
            "offers": {
                "@type": "Offer",
                "price": "1199.00",
                "priceCurrency": "EUR"
            }
        }
        </script>
        </head><body></body></html>
        """
        price = extract_product_price(html, url="https://example.com")
        assert price is not None
        assert price.amount == 1199.00
        assert price.currency == "EUR"
        assert price.description == "iPhone 16 Pro"

    def test_aggregate_offer(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "AggregateOffer",
            "lowPrice": "89.99",
            "priceCurrency": "USD"
        }
        </script>
        </head><body></body></html>
        """
        price = extract_product_price(html)
        assert price is not None
        assert price.amount == 89.99
        assert price.currency == "USD"

    def test_offers_as_list(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "Widget",
            "offers": [
                {"@type": "Offer", "price": "29.99", "priceCurrency": "EUR"},
                {"@type": "Offer", "price": "39.99", "priceCurrency": "EUR"}
            ]
        }
        </script>
        </head><body></body></html>
        """
        price = extract_product_price(html)
        assert price is not None
        assert price.amount == 29.99  # Takes first offer

    def test_graph_array(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@graph": [
                {"@type": "WebPage", "name": "Shop"},
                {
                    "@type": "Product",
                    "name": "Laptop",
                    "offers": {"price": "999", "priceCurrency": "EUR"}
                }
            ]
        }
        </script>
        </head><body></body></html>
        """
        price = extract_product_price(html)
        assert price is not None
        assert price.amount == 999.0

    def test_invalid_json_skipped(self):
        html = """
        <html><head>
        <script type="application/ld+json">NOT VALID JSON</script>
        </head><body></body></html>
        """
        price = extract_product_price(html)
        assert price is None


# ── OpenGraph extraction ──


class TestOpenGraphExtraction:
    def test_og_price(self):
        html = """
        <html><head>
        <meta property="og:title" content="Samsung Galaxy S24">
        <meta property="product:price:amount" content="899.99">
        <meta property="product:price:currency" content="EUR">
        </head><body></body></html>
        """
        price = extract_product_price(html)
        assert price is not None
        assert price.amount == 899.99
        assert price.currency == "EUR"
        assert "Samsung" in price.description

    def test_og_missing_currency_defaults_eur(self):
        html = """
        <html><head>
        <meta property="product:price:amount" content="49.99">
        </head><body></body></html>
        """
        price = extract_product_price(html)
        assert price is not None
        assert price.currency == "EUR"


# ── Microdata extraction ──


class TestMicrodataExtraction:
    def test_itemprop_price(self):
        html = """
        <html><body>
        <span itemprop="name">Headphones</span>
        <span itemprop="price" content="79.99">79,99 €</span>
        <meta itemprop="priceCurrency" content="EUR">
        </body></html>
        """
        price = extract_product_price(html)
        assert price is not None
        assert price.amount == 79.99
        assert price.currency == "EUR"

    def test_itemprop_price_text(self):
        html = """
        <html><body>
        <span itemprop="price">149,99</span>
        </body></html>
        """
        price = extract_product_price(html)
        assert price is not None
        assert price.amount == 149.99


# ── CSS class extraction ──


class TestCssExtraction:
    def test_price_class(self):
        html = """
        <html><body>
        <span class="price">€59.99</span>
        </body></html>
        """
        price = extract_product_price(html)
        assert price is not None
        assert price.amount == 59.99

    def test_data_price_attribute(self):
        html = """
        <html><body>
        <span class="price" data-price="199.99">199,99 €</span>
        </body></html>
        """
        price = extract_product_price(html)
        assert price is not None
        assert price.amount == 199.99


# ── fetch_page ──


class TestFetchPage:
    def test_invalid_url_returns_none(self):
        result = fetch_page("http://this-domain-does-not-exist-12345.com", timeout=2)
        assert result is None

    def test_empty_url_returns_none(self):
        result = fetch_page("", timeout=2)
        assert result is None


# ── search_flight_prices (mocked) ──


class TestSearchFlightPrices:
    def test_returns_list(self):
        """Even with no results, should return an empty list, not crash."""
        # This may hit the network or fail gracefully — both are acceptable
        results = search_flight_prices("DUS", "IST", "2025-04-15", max_results=2)
        assert isinstance(results, list)
