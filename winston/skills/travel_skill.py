"""
Travel Skill - Search flights and hotels using the Amadeus API.
Provides flight offers, hotel listings, and travel information.
"""

import logging
from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.travel")


class TravelSkill(BaseSkill):
    """Search flights and hotels using the Amadeus travel API."""

    name = "travel"
    description = (
        "Search for flights, hotels, and travel information. "
        "Use this for ANY query involving 'Google Flights', 'Booking.com', 'Kayak', "
        "'Skyscanner', 'Expedia', or airlines (Pegasus, Turkish Airlines, etc.). "
        "Also use this when the user wants to find cheap prices for flights between cities."
    )
    parameters = {
        "action": "Action to perform: 'search_flights', 'search_hotels', 'flight_details'",
        "origin": "(search_flights) Origin airport IATA code, e.g. 'FRA'",
        "destination": "(search_flights/search_hotels) Destination IATA code or city code, e.g. 'JFK'",
        "departure_date": "(search_flights) Departure date in YYYY-MM-DD format",
        "return_date": "(search_flights) Optional return date in YYYY-MM-DD format",
        "adults": "(search_flights) Number of adult passengers (default: 1)",
        "max_results": "(search_flights) Maximum number of flight offers to return (default: 5)",
    }

    def __init__(self, config=None):
        super().__init__(config)
        self._client = None

    def _get_client(self):
        """Lazy-initialize the Amadeus client."""
        if self._client is None:
            from amadeus import Amadeus
            hostname = (
                "production"
                if getattr(self.config, "environment", "test") == "production"
                else "test"
            )
            self._client = Amadeus(
                client_id=self.config.api_key,
                client_secret=self.config.api_secret,
                hostname=hostname,
            )
        return self._client

    def execute(self, **kwargs) -> SkillResult:
        """Execute a travel action. Falls back to web search if Amadeus not configured."""
        if not self.config or not getattr(self.config, "api_key", None):
            return self._web_search_fallback(**kwargs)

        action = kwargs.get("action", "search_flights")

        if action == "search_flights":
            return self._search_flights(**kwargs)
        elif action == "search_hotels":
            return self._search_hotels(**kwargs)
        elif action == "flight_details":
            return self._flight_details(**kwargs)
        else:
            return SkillResult(
                success=False,
                message=(
                    f"Unknown travel action: {action}. "
                    "Use 'search_flights', 'search_hotels', or 'flight_details'."
                ),
            )

    # ------------------------------------------------------------------
    # Web search fallback (when Amadeus not configured)
    # ------------------------------------------------------------------

    def _web_search_fallback(self, **kwargs) -> SkillResult:
        """Search flights/hotels via web scraping when Amadeus is not configured."""
        action = kwargs.get("action", "search_flights")

        try:
            from winston.utils.scraper import search_flight_prices, search_hotel_prices
        except ImportError:
            return SkillResult(
                success=False,
                message="Web search fallback not available (beautifulsoup4 not installed).",
            )

        if action == "search_flights":
            origin = kwargs.get("origin", "").strip().upper()
            destination = kwargs.get("destination", "").strip().upper()
            date = kwargs.get("departure_date", "")
            max_results = int(kwargs.get("max_results", 5))

            if not origin or not destination:
                return SkillResult(
                    success=False,
                    message="Missing origin or destination airport code.",
                )

            results = search_flight_prices(origin, destination, date, max_results=max_results)
            if not results:
                return SkillResult(
                    success=True,
                    message=f"No flight prices found for {origin} -> {destination}" + (f" on {date}" if date else "") + ".",
                )

            lines = [f"Flight prices found via web search ({origin} -> {destination}):\n"]
            for i, p in enumerate(results, 1):
                lines.append(f"{i}. **{p.amount:.2f} {p.currency}**\n   {p.description}\n   {p.source}\n")

            # Append direct booking links
            links = [
                f"- **Google Flights**: https://www.google.com/travel/flights?q=Flights%20from%20{origin}%20to%20{destination}%20on%20{date}",
                f"- **Kayak**: https://www.kayak.com/flights/{origin.lower()}-{destination.lower()}/{date}",
                f"- **Skyscanner**: https://www.skyscanner.net/transport/flights/{origin.lower()}/{destination.lower()}/{date}/",
                f"- **Booking.com**: https://flights.booking.com/flights/{origin}-{destination}/?type=ONEWAY&adults=1&cabinClass=ECONOMY&depart={date}",
                f"- **Expedia**: https://www.expedia.com/Flights-Search?mode=search&leg1=from:{origin},to:{destination},departure:{date.replace('-', '/')}&passengers=children:0,adults:1,seniors:0,infantinlap:Y"
            ]
            lines.append("\n🌍 **Direct Search & Compare Links (includes Turkish Airlines, Pegasus, etc.):**\n" + "\n".join(links))

            return SkillResult(
                success=True,
                message="\n".join(lines),
                data=[{"amount": p.amount, "currency": p.currency, "source": p.source} for p in results],
                speak=False,
            )

        elif action == "search_hotels":
            destination = kwargs.get("destination", "").strip().upper()
            if not destination:
                return SkillResult(success=False, message="Missing destination.")

            results = search_hotel_prices(destination, max_results=int(kwargs.get("max_results", 5)))
            if not results:
                return SkillResult(success=True, message=f"No hotel prices found for {destination}.")

            lines = [f"Hotel prices found via web search ({destination}):\n"]
            for i, p in enumerate(results, 1):
                lines.append(f"{i}. **{p.amount:.2f} {p.currency}** per night\n   {p.description}\n   {p.source}\n")

            return SkillResult(
                success=True,
                message="\n".join(lines),
                data=[{"amount": p.amount, "currency": p.currency, "source": p.source} for p in results],
                speak=False,
            )

        return SkillResult(
            success=False,
            message=f"Action '{action}' not supported in web search mode. Try 'search_flights' or 'search_hotels'.",
        )

    # ------------------------------------------------------------------
    # search_flights
    # ------------------------------------------------------------------

    def _search_flights(self, **kwargs) -> SkillResult:
        """Search for flight offers between two airports."""
        origin = kwargs.get("origin", "").strip().upper()
        destination = kwargs.get("destination", "").strip().upper()
        departure_date = kwargs.get("departure_date", "")
        return_date = kwargs.get("return_date")
        adults = int(kwargs.get("adults", 1))
        max_results = int(kwargs.get("max_results", 5))

        error = self.validate_params(
            ["origin", "destination", "departure_date"],
            {"origin": origin, "destination": destination, "departure_date": departure_date},
        )
        if error:
            return SkillResult(success=False, message=error)

        try:
            from amadeus import ResponseError

            client = self._get_client()

            search_params = {
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": departure_date,
                "adults": adults,
                "max": max_results,
            }
            if return_date:
                search_params["returnDate"] = return_date

            response = client.shopping.flight_offers_search.get(**search_params)
            offers = response.data

            if not offers:
                return SkillResult(
                    success=True,
                    message=(
                        f"No flights found from {origin} to {destination} "
                        f"on {departure_date}."
                    ),
                    speak=True,
                )

            formatted = self._format_flight_offers(offers, origin, destination, departure_date, return_date)

            return SkillResult(
                success=True,
                message=formatted,
                data=offers,
                speak=False,
            )

        except ImportError:
            return SkillResult(
                success=False,
                message="Amadeus SDK not installed. Install with: pip install amadeus",
            )
        except ResponseError as e:
            logger.error(f"Amadeus flight search error: {e}")
            return SkillResult(
                success=False,
                message=f"Flight search failed: {e}",
            )
        except Exception as e:
            logger.error(f"Unexpected error during flight search: {e}")
            return SkillResult(
                success=False,
                message=f"Flight search failed: {str(e)}",
            )

    def _format_flight_offers(self, offers, origin, destination, departure_date, return_date):
        """Format a list of Amadeus flight offers into a readable string."""
        trip_type = "round-trip" if return_date else "one-way"
        header = (
            f"Flight results: {origin} -> {destination} "
            f"({departure_date}"
        )
        if return_date:
            header += f" to {return_date}"
        header += f", {trip_type})\n"
        header += "=" * len(header) + "\n\n"

        lines = [header]

        for i, offer in enumerate(offers, 1):
            price_info = offer.get("price", {})
            total = price_info.get("total", "N/A")
            currency = price_info.get("currency", "EUR")

            lines.append(f"--- Offer {i} --- {total} {currency} ---\n")

            itineraries = offer.get("itineraries", [])
            for itin_idx, itinerary in enumerate(itineraries):
                direction = "Outbound" if itin_idx == 0 else "Return"
                duration = itinerary.get("duration", "N/A")
                # Convert ISO 8601 duration to human-readable
                readable_duration = self._format_duration(duration)

                segments = itinerary.get("segments", [])
                num_stops = len(segments) - 1

                if num_stops == 0:
                    stops_label = "Direct"
                elif num_stops == 1:
                    stops_label = "1 stop"
                else:
                    stops_label = f"{num_stops} stops"

                lines.append(f"  {direction} ({readable_duration}, {stops_label}):\n")

                for seg in segments:
                    dep = seg.get("departure", {})
                    arr = seg.get("arrival", {})
                    carrier = seg.get("carrierCode", "??").upper()
                    flight_num = seg.get("number", "")
                    dep_iata = dep.get("iataCode", "???").upper()
                    arr_iata = arr.get("iataCode", "???").upper()
                    dep_time = dep.get("at", "")
                    arr_time = arr.get("at", "")

                    # Format times: "2025-06-15T10:30:00" -> "10:30"
                    dep_short = dep_time[11:16] if len(dep_time) > 15 else dep_time
                    arr_short = arr_time[11:16] if len(arr_time) > 15 else arr_time

                    lines.append(
                        f"    {carrier}{flight_num}: "
                        f"{dep_iata} {dep_short} -> {arr_iata} {arr_short}\n"
                    )

            lines.append("\n")

        # Append direct booking links
        links = [
            f"- **Google Flights**: https://www.google.com/travel/flights?q=Flights%20from%20{origin}%20to%20{destination}%20on%20{departure_date}",
            f"- **Kayak**: https://www.kayak.com/flights/{origin.lower()}-{destination.lower()}/{departure_date}",
            f"- **Skyscanner**: https://www.skyscanner.net/transport/flights/{origin.lower()}/{destination.lower()}/{departure_date}/",
            f"- **Booking.com**: https://flights.booking.com/flights/{origin}-{destination}/?type=ONEWAY&adults=1&cabinClass=ECONOMY&depart={departure_date}",
            f"- **Expedia**: https://www.expedia.com/Flights-Search?mode=search&leg1=from:{origin},to:{destination},departure:{departure_date.replace('-', '/')}&passengers=children:0,adults:1,seniors:0,infantinlap:Y"
        ]
        if return_date:
            # Modify Kayak link for round trips just as an example
            links[1] = f"- **Kayak**: https://www.kayak.com/flights/{origin.lower()}-{destination.lower()}/{departure_date}/{return_date}"
            
        lines.append("\n🌍 **Direct Search & Compare Links (includes Turkish Airlines, Pegasus, etc.):**\n" + "\n".join(links) + "\n")

        return "".join(lines).rstrip() + "\n"

    @staticmethod
    def _format_duration(iso_duration: str) -> str:
        """Convert an ISO 8601 duration like 'PT2H30M' to '2h 30m'."""
        if not iso_duration or not iso_duration.startswith("PT"):
            return iso_duration or "N/A"
        remainder = iso_duration[2:]
        parts = []
        hours = ""
        minutes = ""
        for ch in remainder:
            if ch == "H":
                hours = hours or "0"
                parts.append(f"{hours}h")
                hours = ""
            elif ch == "M":
                minutes = minutes or "0"
                parts.append(f"{minutes}m")
                minutes = ""
            elif ch.isdigit():
                if "H" not in iso_duration[2:] or (hours == "" and "H" in remainder[remainder.index(ch):]):
                    # Still accumulating for hours or minutes
                    pass
                # Simple approach: accumulate digits
                if not parts:
                    hours += ch
                else:
                    minutes += ch
        # Re-do with a cleaner approach
        parts = []
        num = ""
        for ch in iso_duration[2:]:
            if ch.isdigit():
                num += ch
            elif ch == "H":
                parts.append(f"{num}h")
                num = ""
            elif ch == "M":
                parts.append(f"{num}m")
                num = ""
        return " ".join(parts) if parts else iso_duration

    # ------------------------------------------------------------------
    # search_hotels
    # ------------------------------------------------------------------

    def _search_hotels(self, **kwargs) -> SkillResult:
        """Search for hotels in a city."""
        destination = kwargs.get("destination", "").strip().upper()

        if not destination:
            return SkillResult(
                success=False,
                message="Missing required parameter: destination (IATA city code, e.g. 'PAR').",
            )

        try:
            from amadeus import ResponseError

            client = self._get_client()

            response = client.reference_data.locations.hotels.by_city.get(
                cityCode=destination,
            )
            hotels = response.data

            if not hotels:
                return SkillResult(
                    success=True,
                    message=f"No hotels found in {destination}.",
                    speak=True,
                )

            formatted = self._format_hotel_results(hotels, destination)

            return SkillResult(
                success=True,
                message=formatted,
                data=hotels,
                speak=False,
            )

        except ImportError:
            return SkillResult(
                success=False,
                message="Amadeus SDK not installed. Install with: pip install amadeus",
            )
        except ResponseError as e:
            logger.error(f"Amadeus hotel search error: {e}")
            return SkillResult(
                success=False,
                message=f"Hotel search failed: {e}",
            )
        except Exception as e:
            logger.error(f"Unexpected error during hotel search: {e}")
            return SkillResult(
                success=False,
                message=f"Hotel search failed: {str(e)}",
            )

    @staticmethod
    def _format_hotel_results(hotels, city_code):
        """Format Amadeus hotel list into a readable string."""
        header = f"Hotels in {city_code}:\n"
        header += "=" * (len(header) - 1) + "\n\n"

        lines = [header]
        # Limit to a reasonable number of results
        display_hotels = hotels[:20]

        for i, hotel in enumerate(display_hotels, 1):
            name = hotel.get("name", "Unknown Hotel")
            address = hotel.get("address", {})
            country = address.get("countryCode", "")

            address_line = ""
            if address:
                parts = []
                if address.get("lines"):
                    parts.extend(address["lines"])
                if address.get("cityName"):
                    parts.append(address["cityName"])
                if country:
                    parts.append(country)
                address_line = ", ".join(parts)

            lines.append(f"{i}. {name}\n")
            if address_line:
                lines.append(f"   Address: {address_line}\n")
            lines.append("\n")

        total = len(hotels)
        if total > 20:
            lines.append(f"... and {total - 20} more hotels.\n")

        return "".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------------
    # flight_details
    # ------------------------------------------------------------------

    def _flight_details(self, **kwargs) -> SkillResult:
        """Placeholder for detailed flight information."""
        return SkillResult(
            success=True,
            message="Use search_flights to find available flights.",
            speak=True,
        )
