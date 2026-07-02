"""MCP server exposing clubmanager365 court booking as tools.

Two ways to run it:

**Local stdio (the default)** — an MCP client (Claude Desktop, Codex, …)
launches it with the user's own credentials in the environment:

    uvx --from clubmanager365cli[mcp] clubmanager365-mcp

**Remote HTTP (streamable-http)** — set ``CM365_MCP_TRANSPORT=http`` and the
server listens on ``CM365_MCP_HOST``:``CM365_MCP_PORT`` (default
127.0.0.1:8000, endpoint path ``/mcp``). In this mode each request must carry
the caller's own credentials in headers, which is how gateways like Smithery
deliver per-user session config:

    x-cm365-username: <username>
    x-cm365-password: <password>
    x-cm365-base-url: <optional, defaults to https://clubmanager365.com>

In HTTP mode the environment credentials are deliberately IGNORED unless
``CM365_HTTP_ENV_FALLBACK=1`` is set — otherwise anyone reaching a publicly
tunnelled endpoint without headers would silently act as the server owner.

Credentials are used per-request to log in and are never stored server-side.
Booking and cancelling default to a *preview* and only act when ``confirm`` is
true, so an agent cannot book or cancel without an explicit confirmation step.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import List, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .bookings import COURT_TYPES, DEFAULT_MATCH_TYPE, BookingManager, parse_date
from .client import ClubManagerClient
from .config import DEFAULT_BASE_URL, ConfigError, Credentials, load_credentials

mcp = FastMCP("clubmanager365")

# Header names Smithery (or any gateway/client) uses to pass per-user config.
HEADER_USERNAME = "x-cm365-username"
HEADER_PASSWORD = "x-cm365-password"
HEADER_BASE_URL = "x-cm365-base-url"


def _http_mode() -> bool:
    return os.environ.get("CM365_MCP_TRANSPORT", "stdio").lower() in (
        "http",
        "streamable-http",
    )


def _credentials_from_request(ctx: Optional[Context]) -> Optional[Credentials]:
    """Extract per-user credentials from the HTTP request headers, if any."""
    if ctx is None:
        return None
    try:
        request = ctx.request_context.request
    except (AttributeError, LookupError, ValueError):
        return None
    if request is None or not hasattr(request, "headers"):
        return None  # stdio transport
    username = request.headers.get(HEADER_USERNAME)
    password = request.headers.get(HEADER_PASSWORD)
    if not username or not password:
        return None
    base_url = request.headers.get(HEADER_BASE_URL) or DEFAULT_BASE_URL
    return Credentials(
        username=username, password=password, base_url=base_url.rstrip("/")
    )


def _manager(ctx: Optional[Context] = None) -> BookingManager:
    creds = _credentials_from_request(ctx)
    if creds is None:
        if _http_mode() and os.environ.get("CM365_HTTP_ENV_FALLBACK") != "1":
            raise ConfigError(
                "Missing credentials: this server runs in HTTP mode and each "
                f"request must include the '{HEADER_USERNAME}' and "
                f"'{HEADER_PASSWORD}' headers (configure them in your MCP "
                "client / Smithery connection settings)."
            )
        creds = load_credentials()
    client = ClubManagerClient(creds)
    client.login()
    return BookingManager(client)


@mcp.tool()
def list_slots(
    date: str = "today",
    available_only: bool = True,
    court_type: Optional[str] = None,
    ctx: Context = None,
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
    slots = _manager(ctx).get_court_day(parse_date(date), court_types=court_types)
    return [asdict(s) for s in slots if s.available or not available_only]


@mcp.tool()
def my_bookings(ctx: Context = None) -> list:
    """List your upcoming bookings (with their booking ids)."""
    return [asdict(b) for b in _manager(ctx).my_bookings()]


@mcp.tool()
def search_players(query: str, limit: int = 25, ctx: Context = None) -> list:
    """Search the club member directory to find an opponent's name and id."""
    directory = _manager(ctx).players()
    needle = query.lower()
    rows = [{"id": pid, "name": name.title()}
            for name, pid in directory.items() if needle in name]
    return sorted(rows, key=lambda r: r["name"])[:limit]


@mcp.tool()
def list_match_types(ctx: Context = None) -> list:
    """List this club's match types as [{"id", "name"}, …].

    Match-type numbering is club-specific (commonly 4 = Friendly).
    Use the right id for book_court's ``match_type`` or set CM365_MATCH_TYPE.
    """
    return _manager(ctx).match_types()


@mcp.tool()
def book_court(
    date: str,
    time: str,
    opponents: List[str],
    court: Optional[str] = None,
    match_type: str = DEFAULT_MATCH_TYPE,
    confirm: bool = False,
    ctx: Context = None,
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
    mgr = _manager(ctx)
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
def cancel_booking(
    booking_id: int, confirm: bool = False, ctx: Context = None
) -> dict:
    """Cancel a booking by id (get ids from my_bookings).

    Requires ``confirm`` to be true; otherwise returns a confirmation prompt.
    """
    if not confirm:
        return {
            "status": "needs_confirmation",
            "booking_id": booking_id,
            "note": "Call again with confirm=true to cancel this booking.",
        }
    return _manager(ctx).cancel(booking_id)


def main() -> None:
    if _http_mode():
        mcp.settings.host = os.environ.get("CM365_MCP_HOST", "127.0.0.1")
        # CM365_MCP_PORT wins; fall back to PORT, which serverless container
        # platforms (Cloud Run, Fly.io, …) inject automatically.
        mcp.settings.port = int(
            os.environ.get("CM365_MCP_PORT") or os.environ.get("PORT") or 8000
        )
        # Serverless platforms scale to zero and run multiple instances, so
        # in-memory MCP sessions don't survive. CM365_MCP_STATELESS=1 makes
        # every request self-contained (this server keeps no per-session
        # state anyway — credentials arrive with each request).
        if os.environ.get("CM365_MCP_STATELESS") == "1":
            mcp.settings.stateless_http = True
        # The SDK's DNS-rebinding protection rejects any Host header other
        # than localhost with a 421. This server is designed to sit behind a
        # tunnel/reverse proxy whose public hostname is legitimate (and, for
        # quick tunnels, not known in advance), and every sensitive call
        # already requires per-request credential headers — so the protection
        # is off by default. Set CM365_MCP_ALLOWED_HOSTS to a comma-separated
        # hostname list to turn strict Host validation back on.
        allowed = os.environ.get("CM365_MCP_ALLOWED_HOSTS", "").strip()
        if allowed:
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[h.strip() for h in allowed.split(",") if h.strip()],
            )
        else:
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            )
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
