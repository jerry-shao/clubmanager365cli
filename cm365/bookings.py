"""High-level court-booking operations built on the ActionHandler API.

Booking flow (reverse-engineered from Common-min.js):

1. ``GetCourtDay``            – list every court's slots for a date.
2. ``SaveNewPreliminaryBooking`` – place a short-lived hold on a slot;
   returns ``PreliminaryBookingID`` and ``CanProceed``.
3. ``MakeBooking``           – confirm the hold into a real booking.

A preliminary booking auto-expires after a few minutes and can be released
early with ``CancelPreliminaryBooking``.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .client import ActionError, ClubManagerClient

# CourtTypeListBox values from the live page.
COURT_TYPES = {"indoor": "0", "outdoor": "1", "grass": "3"}
# DayPartListBox values.
DAY_PARTS = {"morning": "1", "afternoon": "2", "evening": "3"}
# MatchTypesDropDown id. Defaults to 4 (= Friendly), but the numbering is
# club-specific, so allow it to be overridden per deployment.
DEFAULT_MATCH_TYPE = os.environ.get("CM365_MATCH_TYPE", "4")

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: Optional[str]) -> str:
    if not text:
        return ""
    return _TAG_RE.sub(" ", text).replace("\xa0", " ").strip()


def format_date(date: dt.date) -> str:
    """ClubManager expects e.g. ``27 Jun 2026`` (no leading zero on the day)."""
    return f"{date.day} {date:%b %Y}"


@dataclass
class Slot:
    court_name: str
    court_id: int
    slot_id: int
    time_slot: str          # e.g. "08:00 - 09:00"
    start: str              # e.g. "08:00"
    length_minutes: int
    available: bool
    booking_id: Optional[int]
    summary: str            # human text (opponents, or the time for free slots)
    tooltip: str

    @property
    def label(self) -> str:
        state = "free" if self.available else "booked"
        return f"{self.start} {self.court_name} [{state}]"


@dataclass
class MyBooking:
    booking_id: int
    display_date: str
    display_time: str
    court: str
    summary: str
    can_cancel: bool

    @classmethod
    def from_json(cls, b: Dict[str, Any]) -> "MyBooking":
        return cls(
            booking_id=b.get("BookingID"),
            display_date=b.get("DisplayDate", ""),
            display_time=b.get("DisplayTime", ""),
            court=b.get("Court", ""),
            summary=_strip_html(b.get("MatchSummary", "")),
            can_cancel=bool(b.get("CanCancel")),
        )


class BookingManager:
    def __init__(self, client: ClubManagerClient):
        self.client = client

    # -- reading ---------------------------------------------------------

    def get_court_day(
        self,
        date: dt.date,
        court_types: Optional[List[str]] = None,
        day_parts: Optional[List[str]] = None,
    ) -> List[Slot]:
        """Return all slots (free and booked) for the courts on ``date``."""
        ct = court_types or list(COURT_TYPES.values())
        dp = day_parts or list(DAY_PARTS.values())
        payload = {
            "Date": format_date(date),
            "DayParts": dp,
            "CourtTypes": ct,
            "SpaceAvailable": 1400,
        }
        data = self.client.action("GetCourtDay", payload)
        return self._parse_court_day(data)

    @staticmethod
    def _parse_court_day(data: Dict[str, Any]) -> List[Slot]:
        slots: List[Slot] = []
        for court in data.get("Courts", []):
            court_id = court.get("CourtID")
            if court_id is None:
                continue  # the leftmost "Time" header column
            court_name = court.get("ColumnHeading", "")
            for cell in court.get("Cells", []):
                slot_id = cell.get("CourtSlotID")
                if slot_id is None:
                    continue
                css = cell.get("CssClass") or ""
                time_slot = cell.get("TimeSlot") or _strip_html(cell.get("Summary"))
                start = time_slot.split("-")[0].strip() if time_slot else ""
                available = (
                    "courtavailable" in css
                    and "courtempty" in css
                    and cell.get("BookingID") is None
                )
                slots.append(
                    Slot(
                        court_name=court_name,
                        court_id=court_id,
                        slot_id=slot_id,
                        time_slot=time_slot,
                        start=start,
                        length_minutes=cell.get("LengthInMinutes", 0),
                        available=available,
                        booking_id=cell.get("BookingID"),
                        summary=_strip_html(cell.get("Summary")),
                        tooltip=cell.get("ToolTip") or "",
                    )
                )
        return slots

    def my_bookings(self) -> List[MyBooking]:
        data = self.client.action("GetPlayerBookings", {})
        return [MyBooking.from_json(b) for b in data.get("Bookings", [])]

    # -- player directory (for resolving opponents) ----------------------

    def players(self) -> Dict[str, int]:
        """Map ``lower-case full name`` -> player id, from the dashboard.

        Every club member appears on ``MyDashboard.aspx`` as a row carrying
        ``playerid='…' data-filtername='full name'``.
        """
        html = self.client.get("Club/MyDashboard.aspx").html
        out: Dict[str, int] = {}
        for pid, name in re.findall(
            r"playerid='(\d+)'\s+data-filtername=\"([^\"]+)\"", html
        ):
            out[name.strip().lower()] = int(pid)
        return out

    def match_types(self) -> List[Dict[str, str]]:
        """List the club's match types as ``[{"id", "name"}, …]``.

        The numbering is club-specific (commonly 4 = Friendly), so this is
        how a different club discovers the right value for the
        ``book`` ``match_type`` argument / the ``CM365_MATCH_TYPE`` env var.
        Parsed from the ``MatchTypesDropDown`` on the bookings page.
        """
        soup = self.client.get("Club/Bookings.aspx").soup
        select = None
        for el in soup.find_all("select"):
            name = el.get("name", "")
            if "MatchTypesDropDown" in name and "Bulk" not in name:
                select = el
                break
        if select is None:
            return []
        out: List[Dict[str, str]] = []
        for opt in select.find_all("option"):
            value = opt.get("value")
            if value is None:
                continue
            out.append({"id": value, "name": opt.get_text(strip=True)})
        out.sort(key=lambda r: int(r["id"]) if r["id"].isdigit() else 0)
        return out

    def resolve_opponents(self, values: List[str]) -> List[str]:
        """Turn names or numeric ids into a list of player-id strings."""
        directory: Optional[Dict[str, int]] = None
        ids: List[str] = []
        for v in values:
            v = v.strip()
            if v.isdigit():
                ids.append(v)
                continue
            if directory is None:
                directory = self.players()
            key = v.lower()
            if key in directory:
                ids.append(str(directory[key]))
                continue
            matches = [n for n in directory if key in n]
            if len(matches) == 1:
                ids.append(str(directory[matches[0]]))
            elif not matches:
                raise ActionError(f"No club member matches opponent {v!r}.")
            else:
                preview = ", ".join(sorted(matches)[:8])
                raise ActionError(
                    f"Opponent {v!r} is ambiguous ({len(matches)} matches): {preview} …"
                )
        return ids

    # -- booking ---------------------------------------------------------

    def find_slot(
        self,
        date: dt.date,
        start: str,
        court: Optional[str] = None,
        court_types: Optional[List[str]] = None,
    ) -> Optional[Slot]:
        """Find an available slot at ``start`` (e.g. "18:00"), optional court."""
        start = _normalise_time(start)
        slots = self.get_court_day(date, court_types=court_types)
        candidates = [
            s for s in slots if s.available and _normalise_time(s.start) == start
        ]
        if court:
            needle = court.lower()
            candidates = [c for c in candidates if needle in c.court_name.lower()]
        return candidates[0] if candidates else None

    def save_preliminary(
        self, slot: Slot, date: dt.date, player_id: Optional[int] = None
    ) -> Dict[str, Any]:
        payload = {
            "BookingPlayerID": player_id,
            "MatchDate": format_date(date),
            "CourtID": slot.court_id,
            "CourtSlotID": slot.slot_id,
        }
        return self.client.action("SaveNewPreliminaryBooking", payload)

    def cancel_preliminary(self, preliminary_id: Any) -> Dict[str, Any]:
        return self.client.action(
            "CancelPreliminaryBooking", {"PreliminaryBookingID": preliminary_id}
        )

    def make_booking(
        self,
        slot: Slot,
        date: dt.date,
        opponent_ids: List[str],
        match_type: str = DEFAULT_MATCH_TYPE,
    ) -> Dict[str, Any]:
        """Make a free booking in a single call (the verified flow).

        Self free bookings need neither ``BookingPlayerID`` nor a preliminary
        hold. Ids are sent as strings, matching the site's own requests.
        """
        payload = {
            "OpponentPlayerIDs": [str(o) for o in opponent_ids],
            "CourtsRequired": [{"c": str(slot.court_id), "s": str(slot.slot_id)}],
            "Notification": "-1",
            "Resources": [],
            "MatchDate": format_date(date),
            "ExpectedBalanceAmount": "",
            "PaymentAmount": 0,
            "SelectedMatchType": str(match_type),
            "ExtensionCourtSlotID": "0",
            "CourtID": str(slot.court_id),
            "GuestItem1": "", "GuestItem2": "", "GuestItem3": "",
            "PackageItem1": "", "PackageItem2": "", "PackageItem3": "",
        }
        result = self.client.action("MakeBooking", payload)
        if not result.get("WasSuccessful", False):
            raise ActionError(
                result.get("ErrorMessage") or "MakeBooking failed (unknown error)."
            )
        return result

    def book(
        self,
        date: dt.date,
        start: str,
        opponents: Optional[List[str]] = None,
        court: Optional[str] = None,
        court_types: Optional[List[str]] = None,
        match_type: str = DEFAULT_MATCH_TYPE,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Book a free slot. ``dry_run=True`` only locates the slot.

        ``opponents`` is a list of names or numeric ids (this club requires at
        least one opponent for every booking).
        """
        slot = self.find_slot(date, start, court=court, court_types=court_types)
        if slot is None:
            raise ActionError(
                f"No available slot at {start} on {format_date(date)}"
                + (f" for court matching '{court}'." if court else ".")
            )
        if dry_run:
            return {"status": "found", "slot": slot}

        opponent_ids = self.resolve_opponents(opponents or [])
        if not opponent_ids:
            raise ActionError(
                "This club requires at least one opponent. Pass --with <name|id>."
            )
        result = self.make_booking(slot, date, opponent_ids, match_type=match_type)
        return {"status": "booked", "slot": slot, "result": result}

    def cancel(self, booking_id: int) -> Dict[str, Any]:
        result = self.client.action("CancelBooking", {"BookingID": booking_id})
        if not result.get("WasSuccessful", True):
            raise ActionError(result.get("ErrorMessage") or "CancelBooking failed.")
        return result


def _normalise_time(value: str) -> str:
    """Accept "18", "18:00", "6pm" → "18:00" for comparison."""
    value = value.strip().lower()
    m = re.match(r"^(\d{1,2})(?::?(\d{2}))?\s*(am|pm)?$", value)
    if not m:
        return value
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def parse_date(value: str) -> dt.date:
    """Parse a date the CLI accepts: today/tomorrow, YYYY-MM-DD, or '27 Jun 2026'."""
    value = value.strip().lower()
    today = dt.date.today()
    if value in ("today", "今天"):
        return today
    if value in ("tomorrow", "明天"):
        return today + dt.timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {value!r}")
