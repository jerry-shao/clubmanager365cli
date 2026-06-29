"""MCP server exposing clubmanager365 court booking as tools.

Run it over stdio (the default) so an MCP client — Claude Desktop, Claude
Code, etc. — can launch it locally with the user's own credentials:

    uvx --from clubmanager365cli[mcp] clubmanager365-mcp

Credentials come from the environment (``CM365_USERNAME`` / ``CM365_PASSWORD``),
set per-user in the MCP client config — never hard-coded. Each tool call logs
in fresh, which keeps the server stateless and avoids stale-session bugs.

Booking and cancelling default to a *preview* and only act when ``confirm`` is
true, so an agent cannot book or cancel without an explicit confirmation step.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from .bookings import COURT_TYPES, DEFAULT_MATCH_TYPE, BookingManager, parse_date
from .client import ClubManagerClient
from .config import load_credentials

mcp = FastMCP("clubmanager365")


def _manager() -> BookingManager:
    client = ClubManagerClient(load_credentials())
    client.login()
    return BookingManager(client)


@mcp.tool()
def list_slots(
    date: str = "today",
    available_only: bool = True,
    court_type: Optional[str] = None,
) -> list:
    """List court slots for a date.

    Args:
        date: "today", "tomorrow", "YYYY-MM-DD", or e.g. "27 Jun 2026".
        available_only: if true, only return free slots.
        court_type: optional filter — "indoor", "outdoor", or "grass".
    """
    court_types = None
    if court_type:
        key = court_type.lower()
        if key not in COURT_TYPES:
            raise ValueError(f"court_type must be one of {', '.join(COURT_TYPES)}")
        court_types = [COURT_TYPES[key]]
    slots = _manager().get_court_day(parse_date(date), court_types=court_types)
    return [asdict(s) for s in slots if s.available or not available_only]


@mcp.tool()
def my_bookings() -> list:
    """List your upcoming bookings (with their booking ids)."""
    return [asdict(b) for b in _manager().my_bookings()]


@mcp.tool()
def search_players(query: str, limit: int = 25) -> list:
    """Search the club member directory to find an opponent's name and id."""
    directory = _manager().players()
    needle = query.lower()
    rows = [{"id": pid, "name": name.title()}
            for name, pid in directory.items() if needle in name]
    return sorted(rows, key=lambda r: r["name"])[:limit]


@mcp.tool()
def list_match_types() -> list:
    """List this club's match types as [{"id", "name"}, …].

    Match-type numbering is club-specific (commonly 4 = Friendly).
    Use the right id for book_court's ``match_type`` or set CM365_MATCH_TYPE.
    """
    return _manager().match_types()


@mcp.tool()
def book_court(
    date: str,
    time: str,
    opponents: List[str],
    court: Optional[str] = None,
    match_type: str = DEFAULT_MATCH_TYPE,
    confirm: bool = False,
) -> dict:
    """Book a free court slot.

    Returns a PREVIEW of the matched slot unless ``confirm`` is true, in which
    case the booking is actually made. Most clubs require at least one
    opponent.

    Args:
        date: "today", "tomorrow", "YYYY-MM-DD", or "27 Jun 2026".
        time: start time, e.g. "18:00", "18", or "6pm".
        opponents: club member names (fuzzy) and/or numeric player ids.
        court: optional court-name filter, e.g. "Indoor Court 1".
        match_type: club-specific match-type id (default from CM365_MATCH_TYPE).
        confirm: must be true to actually book; otherwise a dry run.
    """
    mgr = _manager()
    result = mgr.book(
        parse_date(date),
        time,
        opponents=opponents,
        court=court,
        match_type=match_type,
        dry_run=not confirm,
    )
    out = {"status": result["status"], "slot": asdict(result["slot"])}
    if not confirm:
        out["note"] = "Dry run. Call again with confirm=true to book this slot."
    return out


@mcp.tool()
def cancel_booking(booking_id: int, confirm: bool = False) -> dict:
    """Cancel a booking by id (get ids from my_bookings).

    Requires ``confirm`` to be true; otherwise returns a confirmation prompt.
    """
    if not confirm:
        return {
            "status": "needs_confirmation",
            "booking_id": booking_id,
            "note": "Call again with confirm=true to cancel this booking.",
        }
    return _manager().cancel(booking_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
