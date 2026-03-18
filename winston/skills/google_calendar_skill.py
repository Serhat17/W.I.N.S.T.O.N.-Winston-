"""
Google Calendar Skill - Manage events via the Google Calendar API.
Supports listing, creating, updating, deleting events, checking availability,
and booking appointments with conflict detection.

Requires:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # Python < 3.9

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.google_calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarSkill(BaseSkill):
    """Manage Google Calendar events via the Google Calendar API."""

    name = "google_calendar"
    description = (
        "Manage Google Calendar: list upcoming events, create new events, "
        "update or delete existing events, check availability for a time slot, "
        "and book appointments with automatic conflict detection. "
        "Use this when the user wants to interact with their Google Calendar."
    )
    parameters = {
        "action": (
            "Action to perform: 'list_events', 'create_event', 'update_event', "
            "'delete_event', 'check_availability', 'book_appointment'"
        ),
        "title": "(create_event/update_event/book_appointment) Event title",
        "date": "(create_event/update_event/check_availability/book_appointment) Date in YYYY-MM-DD format",
        "time": "(create_event/update_event/check_availability/book_appointment) Time in HH:MM format (24h)",
        "duration": "(create_event/check_availability/book_appointment) Duration in minutes (default: 60)",
        "description": "(create_event/update_event) Event description",
        "location": "(create_event/update_event) Event location",
        "attendees": "(create_event/book_appointment) Comma-separated email addresses of attendees",
        "event_id": "(update_event/delete_event) Google Calendar event ID",
        "days": "(list_events) Number of days to look ahead (default: 7)",
        "end_time": "(check_availability) End time in HH:MM format for availability check",
    }

    def __init__(self, config=None):
        super().__init__(config)
        self._service = None
        self._timezone = "UTC"

        # Attempt to detect system timezone
        try:
            tz_now = datetime.now().astimezone()
            self._timezone = str(tz_now.tzinfo)
        except Exception:
            pass

        # Allow config to override timezone
        if self.config and hasattr(self.config, "timezone") and self.config.timezone:
            self._timezone = self.config.timezone

    # ------------------------------------------------------------------
    # Google API service (lazy-initialised)
    # ------------------------------------------------------------------

    def _get_service(self):
        """Lazy-initialize and return the Google Calendar API service."""
        if self._service is not None:
            return self._service

        # Guard against missing packages
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Google API packages are not installed. "
                "Run: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            ) from exc

        if not self.config:
            raise RuntimeError(
                "GoogleCalendarSkill requires a config object with "
                "credentials_file, token_file, and calendar_id attributes."
            )

        credentials_file = Path(self.config.credentials_file).expanduser()
        token_file = Path(self.config.token_file).expanduser()

        if not credentials_file.exists():
            raise RuntimeError(
                f"Google credentials file not found: {credentials_file}. "
                "Download it from the Google Cloud Console (OAuth 2.0 Client ID)."
            )

        creds: Optional[Credentials] = None

        # Load existing token
        if token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
            except Exception as exc:
                logger.warning("Failed to load token file, will re-authenticate: %s", exc)
                creds = None

        # Refresh or run OAuth flow
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("Google Calendar token refreshed successfully.")
            except Exception as exc:
                logger.warning("Token refresh failed, will re-authenticate: %s", exc)
                creds = None

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_file), SCOPES
            )
            creds = flow.run_local_server(port=0)
            logger.info("Google Calendar OAuth flow completed.")

        # Persist the token for next time
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json())

        self._service = build("calendar", "v3", credentials=creds)
        return self._service

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _calendar_id(self) -> str:
        """Return the configured calendar ID or fall back to 'primary'."""
        if self.config and hasattr(self.config, "calendar_id") and self.config.calendar_id:
            return self.config.calendar_id
        return "primary"

    def _make_datetime(self, date_str: str, time_str: str) -> datetime:
        """Parse date (YYYY-MM-DD) and time (HH:MM) into a timezone-aware datetime."""
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        try:
            tz = ZoneInfo(self._timezone)
            dt = dt.replace(tzinfo=tz)
        except Exception:
            pass
        return dt

    def _format_iso(self, dt: datetime) -> str:
        """Format a datetime as an ISO-8601 string suitable for the Google API."""
        return dt.isoformat()

    @staticmethod
    def _format_event_for_display(event: dict) -> str:
        """Format a single Google Calendar event dict into a human-readable string."""
        title = event.get("summary", "(No title)")
        location = event.get("location", "")
        start = event.get("start", {})
        end = event.get("end", {})

        # All-day events use 'date'; timed events use 'dateTime'
        if "dateTime" in start:
            start_dt = datetime.fromisoformat(start["dateTime"])
            end_dt = datetime.fromisoformat(end["dateTime"])
            date_display = start_dt.strftime("%A, %B %d, %Y")
            time_display = f"{start_dt.strftime('%I:%M %p')} - {end_dt.strftime('%I:%M %p')}"
        elif "date" in start:
            date_display = start["date"]
            time_display = "All day"
        else:
            date_display = "Unknown date"
            time_display = ""

        parts = [f"  {title}"]
        parts.append(f"    Date: {date_display}")
        if time_display:
            parts.append(f"    Time: {time_display}")
        if location:
            parts.append(f"    Location: {location}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def execute(self, **kwargs) -> SkillResult:
        """Route to the requested action."""
        action = kwargs.get("action", "list_events")

        actions = {
            "list_events": self._list_events,
            "create_event": self._create_event,
            "update_event": self._update_event,
            "delete_event": self._delete_event,
            "check_availability": self._check_availability,
            "book_appointment": self._book_appointment,
        }

        handler = actions.get(action)
        if not handler:
            return SkillResult(
                success=False,
                message=(
                    f"Unknown action: '{action}'. "
                    f"Valid actions: {', '.join(actions.keys())}"
                ),
            )

        try:
            return handler(**kwargs)
        except RuntimeError as exc:
            # Config / import errors surfaced from _get_service
            logger.error("Google Calendar skill error: %s", exc)
            return SkillResult(success=False, message=str(exc))
        except Exception as exc:
            logger.error("Google Calendar API error: %s", exc, exc_info=True)
            return SkillResult(
                success=False,
                message=f"Google Calendar error: {exc}",
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _list_events(self, **kwargs) -> SkillResult:
        """List upcoming events for the next N days."""
        days = int(kwargs.get("days", 7))
        service = self._get_service()

        now = datetime.now().astimezone()
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days)).isoformat()

        result = (
            service.events()
            .list(
                calendarId=self._calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = result.get("items", [])

        if not events:
            return SkillResult(
                success=True,
                message=f"No upcoming events in the next {days} days.",
            )

        response = f"Upcoming events (next {days} days):\n\n"
        for event in events:
            response += self._format_event_for_display(event) + "\n\n"

        return SkillResult(
            success=True,
            message=response.rstrip(),
            data=events,
            speak=False,
        )

    def _create_event(self, **kwargs) -> SkillResult:
        """Create a new calendar event."""
        title = kwargs.get("title", "")
        if not title:
            return SkillResult(success=False, message="No event title provided.")

        date_str = kwargs.get("date", "")
        time_str = kwargs.get("time", "")
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        if not time_str:
            time_str = "09:00"

        duration = int(kwargs.get("duration", 60))
        description = kwargs.get("description", "")
        location = kwargs.get("location", "")
        attendees_raw = kwargs.get("attendees", "")

        start_dt = self._make_datetime(date_str, time_str)
        end_dt = start_dt + timedelta(minutes=duration)

        event_body: dict[str, Any] = {
            "summary": title,
            "start": {
                "dateTime": self._format_iso(start_dt),
                "timeZone": self._timezone,
            },
            "end": {
                "dateTime": self._format_iso(end_dt),
                "timeZone": self._timezone,
            },
        }

        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location
        if attendees_raw:
            emails = [e.strip() for e in attendees_raw.split(",") if e.strip()]
            event_body["attendees"] = [{"email": addr} for addr in emails]

        service = self._get_service()
        created = (
            service.events()
            .insert(calendarId=self._calendar_id, body=event_body)
            .execute()
        )

        event_link = created.get("htmlLink", "")
        formatted_start = start_dt.strftime("%A, %B %d, %Y at %I:%M %p")

        message = (
            f"Event created: '{title}' on {formatted_start} ({duration} min)."
        )
        if event_link:
            message += f"\nLink: {event_link}"

        return SkillResult(success=True, message=message, data=created)

    def _update_event(self, **kwargs) -> SkillResult:
        """Update (patch) an existing event by event_id."""
        event_id = kwargs.get("event_id", "")
        if not event_id:
            return SkillResult(
                success=False,
                message="No event_id provided. Specify the event to update.",
            )

        service = self._get_service()

        # Build the patch body from provided optional fields
        patch_body: dict[str, Any] = {}

        title = kwargs.get("title")
        if title:
            patch_body["summary"] = title

        description = kwargs.get("description")
        if description:
            patch_body["description"] = description

        location = kwargs.get("location")
        if location:
            patch_body["location"] = location

        date_str = kwargs.get("date")
        time_str = kwargs.get("time")
        if date_str or time_str:
            # Fetch existing event to preserve unchanged start/end fields
            existing = (
                service.events()
                .get(calendarId=self._calendar_id, eventId=event_id)
                .execute()
            )
            existing_start = existing.get("start", {}).get("dateTime", "")
            if existing_start:
                existing_dt = datetime.fromisoformat(existing_start)
            else:
                existing_dt = datetime.now().astimezone()

            new_date = date_str if date_str else existing_dt.strftime("%Y-%m-%d")
            new_time = time_str if time_str else existing_dt.strftime("%H:%M")
            new_start = self._make_datetime(new_date, new_time)

            # Preserve original duration
            existing_end_str = existing.get("end", {}).get("dateTime", "")
            if existing_end_str:
                existing_end_dt = datetime.fromisoformat(existing_end_str)
                original_duration = existing_end_dt - existing_dt
            else:
                original_duration = timedelta(hours=1)
            new_end = new_start + original_duration

            patch_body["start"] = {
                "dateTime": self._format_iso(new_start),
                "timeZone": self._timezone,
            }
            patch_body["end"] = {
                "dateTime": self._format_iso(new_end),
                "timeZone": self._timezone,
            }

        if not patch_body:
            return SkillResult(
                success=False,
                message="No fields to update. Provide at least one of: title, date, time, description, location.",
            )

        updated = (
            service.events()
            .patch(calendarId=self._calendar_id, eventId=event_id, body=patch_body)
            .execute()
        )

        return SkillResult(
            success=True,
            message=f"Event '{updated.get('summary', event_id)}' updated successfully.",
            data=updated,
        )

    def _delete_event(self, **kwargs) -> SkillResult:
        """Delete a calendar event by event_id."""
        event_id = kwargs.get("event_id", "")
        if not event_id:
            return SkillResult(
                success=False,
                message="No event_id provided. Specify the event to delete.",
            )

        service = self._get_service()

        # Fetch event summary before deletion for a friendlier message
        try:
            event = (
                service.events()
                .get(calendarId=self._calendar_id, eventId=event_id)
                .execute()
            )
            event_title = event.get("summary", event_id)
        except Exception:
            event_title = event_id

        service.events().delete(
            calendarId=self._calendar_id, eventId=event_id
        ).execute()

        return SkillResult(
            success=True,
            message=f"Event '{event_title}' deleted successfully.",
        )

    def _check_availability(self, **kwargs) -> SkillResult:
        """Check free/busy status for a given date/time range."""
        date_str = kwargs.get("date", "")
        time_str = kwargs.get("time", "")
        if not date_str or not time_str:
            return SkillResult(
                success=False,
                message="Please provide both 'date' (YYYY-MM-DD) and 'time' (HH:MM) to check availability.",
            )

        duration = int(kwargs.get("duration", 60))
        end_time_str = kwargs.get("end_time", "")

        start_dt = self._make_datetime(date_str, time_str)
        if end_time_str:
            end_dt = self._make_datetime(date_str, end_time_str)
        else:
            end_dt = start_dt + timedelta(minutes=duration)

        service = self._get_service()

        freebusy_query = {
            "timeMin": self._format_iso(start_dt),
            "timeMax": self._format_iso(end_dt),
            "timeZone": self._timezone,
            "items": [{"id": self._calendar_id}],
        }

        result = service.freebusy().query(body=freebusy_query).execute()

        calendars = result.get("calendars", {})
        cal_info = calendars.get(self._calendar_id, {})
        busy_slots = cal_info.get("busy", [])

        formatted_start = start_dt.strftime("%A, %B %d at %I:%M %p")
        formatted_end = end_dt.strftime("%I:%M %p")

        if not busy_slots:
            return SkillResult(
                success=True,
                message=f"You are free on {formatted_start} - {formatted_end}.",
                data={"free": True, "busy_slots": []},
            )

        # Format conflicting slots
        conflicts = []
        for slot in busy_slots:
            busy_start = datetime.fromisoformat(slot["start"])
            busy_end = datetime.fromisoformat(slot["end"])
            conflicts.append(
                f"  {busy_start.strftime('%I:%M %p')} - {busy_end.strftime('%I:%M %p')}"
            )

        conflict_text = "\n".join(conflicts)
        return SkillResult(
            success=True,
            message=(
                f"You are busy on {formatted_start} - {formatted_end}.\n"
                f"Conflicting slots:\n{conflict_text}"
            ),
            data={"free": False, "busy_slots": busy_slots},
        )

    def _book_appointment(self, **kwargs) -> SkillResult:
        """Check availability first, then create the event if the slot is free."""
        title = kwargs.get("title", "")
        date_str = kwargs.get("date", "")
        time_str = kwargs.get("time", "")

        if not title:
            return SkillResult(success=False, message="No event title provided.")
        if not date_str or not time_str:
            return SkillResult(
                success=False,
                message="Please provide both 'date' (YYYY-MM-DD) and 'time' (HH:MM) to book an appointment.",
            )

        # Check availability first
        availability = self._check_availability(**kwargs)
        if not availability.success:
            return availability

        if availability.data and not availability.data.get("free", True):
            busy_slots = availability.data.get("busy_slots", [])
            conflicts = []
            for slot in busy_slots:
                busy_start = datetime.fromisoformat(slot["start"])
                busy_end = datetime.fromisoformat(slot["end"])
                conflicts.append(
                    f"  {busy_start.strftime('%I:%M %p')} - {busy_end.strftime('%I:%M %p')}"
                )
            conflict_text = "\n".join(conflicts)
            return SkillResult(
                success=False,
                message=(
                    f"Cannot book '{title}' -- the requested time slot is already occupied.\n"
                    f"Conflicting slots:\n{conflict_text}"
                ),
                data={"conflict": True, "busy_slots": busy_slots},
            )

        # Slot is free -- create the event
        return self._create_event(**kwargs)
