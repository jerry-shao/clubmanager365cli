"""Command line interface for clubmanager365.

Usage:
    cm365 login                  # verify credentials work
    cm365 explore [PATH] [-o F]  # log in, fetch a page, dump HTML + links
    cm365 whoami                 # show landing page after login
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from urllib.parse import urljoin

# The system Python on macOS links LibreSSL, which urllib3 warns about. The
# warning is harmless for our use; keep CLI output clean.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

from .bookings import BookingManager, COURT_TYPES, DEFAULT_MATCH_TYPE, parse_date
from .client import ActionError, ClubManagerClient, LoginError, Page
from .config import ConfigError, load_credentials


def _client() -> ClubManagerClient:
    try:
        creds = load_credentials()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    return ClubManagerClient(creds)


def _login_or_exit(client: ClubManagerClient) -> Page:
    try:
        return client.login()
    except LoginError as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


def cmd_login(args: argparse.Namespace) -> int:
    client = _client()
    page = _login_or_exit(client)
    print("login OK")
    print(f"  landed on: {page.url}")
    print(f"  title:     {page.title!r}")
    print(f"  cookies:   {', '.join(client.session.cookies.keys())}")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    client = _client()
    page = _login_or_exit(client)
    print(f"landed on: {page.url}")
    print(f"title:     {page.title!r}")
    print("\nnavigation links found:")
    for a in page.soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if not text or href.startswith("#") or href.startswith("javascript"):
            continue
        print(f"  {text:<40} -> {urljoin(page.url, href)}")
    return 0


def cmd_explore(args: argparse.Namespace) -> int:
    client = _client()
    landing = _login_or_exit(client)
    page = client.get(args.path) if args.path else landing

    out = Path(args.output) if args.output else None
    if out:
        out.write_text(page.html, encoding="utf-8")
        print(f"saved {len(page.html)} bytes -> {out}")

    print(f"url:    {page.url}")
    print(f"status: {page.status_code}")
    print(f"title:  {page.title!r}")

    forms = page.soup.find_all("form")
    print(f"\nforms: {len(forms)}")
    for i, form in enumerate(forms):
        action = form.get("action", "")
        method = form.get("method", "get")
        fid = form.get("id", "")
        print(f"  [{i}] id={fid!r} method={method} action={action!r}")

    print("\nlinks:")
    seen = set()
    for a in page.soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("#") or href.startswith("javascript") or href in seen:
            continue
        seen.add(href)
        text = a.get_text(strip=True)
        print(f"  {text[:35]:<35} -> {urljoin(page.url, href)}")
    return 0


def _court_types(arg: str):
    if not arg:
        return None
    out = []
    for name in arg.split(","):
        key = name.strip().lower()
        if key not in COURT_TYPES:
            raise SystemExit(f"unknown court type {name!r}; choose from {', '.join(COURT_TYPES)}")
        out.append(COURT_TYPES[key])
    return out


def cmd_slots(args: argparse.Namespace) -> int:
    client = _client()
    _login_or_exit(client)
    mgr = BookingManager(client)
    date = parse_date(args.date)
    try:
        slots = mgr.get_court_day(date, court_types=_court_types(args.type))
    except ActionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.available:
        slots = [s for s in slots if s.available]
    if args.time:
        from .bookings import _normalise_time
        want = _normalise_time(args.time)
        slots = [s for s in slots if _normalise_time(s.start) == want]

    print(f"{date:%a %d %b %Y} — {len(slots)} slot(s)"
          + (" (available only)" if args.available else ""))
    for s in slots:
        mark = "·" if s.available else "✗"
        extra = "" if s.available else f"  {s.summary}"
        print(f"  {mark} {s.start}  {s.court_name:<18} {s.time_slot}{extra}")
    return 0


def cmd_mybookings(args: argparse.Namespace) -> int:
    client = _client()
    _login_or_exit(client)
    mgr = BookingManager(client)
    bookings = mgr.my_bookings()
    if not bookings:
        print("no upcoming bookings")
        return 0
    print(f"{len(bookings)} booking(s):")
    for b in bookings:
        cancel = " (cancellable)" if b.can_cancel else ""
        print(f"  #{b.booking_id}  {b.display_date} {b.display_time}  "
              f"{b.court}  {b.summary}{cancel}")
    return 0


def _opponents(arg) -> list:
    out = []
    for item in arg or []:
        out.extend(p.strip() for p in item.split(",") if p.strip())
    return out


def cmd_book(args: argparse.Namespace) -> int:
    client = _client()
    _login_or_exit(client)
    mgr = BookingManager(client)
    date = parse_date(args.date)
    opponents = _opponents(args.with_)
    try:
        if args.dry_run or not args.yes:
            res = mgr.book(date, args.time, court=args.court,
                           court_types=_court_types(args.type), dry_run=True)
            slot = res["slot"]
            print(f"found available slot: {slot.start} {slot.court_name} "
                  f"({slot.time_slot}) on {date:%a %d %b %Y}")
            if not args.yes:
                print("\nThis was a dry run. Re-run with --yes (and --with <opponent>) "
                      "to actually book it.")
                return 0
        result = mgr.book(date, args.time, opponents=opponents, court=args.court,
                          court_types=_court_types(args.type), match_type=args.match_type)
        slot = result["slot"]
        print(f"✓ booked {slot.start} {slot.court_name} ({slot.time_slot}) "
              f"on {date:%a %d %b %Y}")
    except ActionError as exc:
        print(f"booking failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_match_types(args: argparse.Namespace) -> int:
    client = _client()
    _login_or_exit(client)
    mgr = BookingManager(client)
    rows = mgr.match_types()
    if not rows:
        print("no match types found (the page layout may have changed)")
        return 1
    print(f"{len(rows)} match type(s):")
    for r in rows:
        marker = "  (default)" if r["id"] == DEFAULT_MATCH_TYPE else ""
        print(f"  {r['id']:<4} {r['name']}{marker}")
    print("\nUse an id with: book ... --match-type <id>, or set CM365_MATCH_TYPE.")
    return 0


def cmd_players(args: argparse.Namespace) -> int:
    client = _client()
    _login_or_exit(client)
    mgr = BookingManager(client)
    directory = mgr.players()
    needle = (args.query or "").lower()
    rows = [(name, pid) for name, pid in directory.items() if needle in name]
    for name, pid in sorted(rows)[:args.limit]:
        print(f"  {pid:<8} {name.title()}")
    print(f"\n{len(rows)} match(es)" + (f", showing {min(len(rows), args.limit)}" if rows else ""))
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    client = _client()
    _login_or_exit(client)
    mgr = BookingManager(client)
    if not args.yes:
        print(f"This will cancel booking #{args.booking_id}. "
              f"Re-run with --yes to confirm.")
        return 0
    try:
        mgr.cancel(args.booking_id)
        print(f"✓ cancelled booking #{args.booking_id}")
    except ActionError as exc:
        print(f"cancel failed: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cm365", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="verify credentials")
    p_login.set_defaults(func=cmd_login)

    p_whoami = sub.add_parser("whoami", help="show post-login landing page + links")
    p_whoami.set_defaults(func=cmd_whoami)

    p_explore = sub.add_parser(
        "explore", help="log in, fetch a page, dump HTML/forms/links"
    )
    p_explore.add_argument(
        "path", nargs="?", help="page to fetch after login (default: landing page)"
    )
    p_explore.add_argument("-o", "--output", help="write fetched HTML to this file")
    p_explore.set_defaults(func=cmd_explore)

    p_slots = sub.add_parser("slots", help="list court slots for a date")
    p_slots.add_argument("date", nargs="?", default="today",
                         help="today | tomorrow | YYYY-MM-DD | '27 Jun 2026'")
    p_slots.add_argument("-a", "--available", action="store_true",
                         help="show only free slots")
    p_slots.add_argument("-t", "--time", help="filter to a start time, e.g. 18:00")
    p_slots.add_argument("--type", help="court types: indoor,outdoor,grass")
    p_slots.set_defaults(func=cmd_slots)

    p_my = sub.add_parser("mybookings", help="list your upcoming bookings")
    p_my.set_defaults(func=cmd_mybookings)

    p_book = sub.add_parser("book", help="book a free slot")
    p_book.add_argument("date", help="today | tomorrow | YYYY-MM-DD | '27 Jun 2026'")
    p_book.add_argument("time", help="start time, e.g. 18:00")
    p_book.add_argument("-w", "--with", dest="with_", action="append", metavar="OPPONENT",
                        help="opponent name or player id (required to book; repeatable "
                             "or comma-separated)")
    p_book.add_argument("-c", "--court", help="court name filter, e.g. 'Indoor Court 1'")
    p_book.add_argument("--type", help="court types: indoor,outdoor,grass")
    p_book.add_argument("--match-type", default="4",
                        help="match type id (default 4 = Friendly)")
    p_book.add_argument("--dry-run", action="store_true",
                        help="only find the slot, do not book")
    p_book.add_argument("--yes", action="store_true",
                        help="actually make the booking (otherwise dry run)")
    p_book.set_defaults(func=cmd_book)

    p_mt = sub.add_parser("match-types", help="list the club's match types (id + name)")
    p_mt.set_defaults(func=cmd_match_types)

    p_players = sub.add_parser("players", help="search the club member directory")
    p_players.add_argument("query", nargs="?", default="", help="name substring")
    p_players.add_argument("-n", "--limit", type=int, default=30, help="max rows")
    p_players.set_defaults(func=cmd_players)

    p_cancel = sub.add_parser("cancel", help="cancel a booking by id")
    p_cancel.add_argument("booking_id", type=int)
    p_cancel.add_argument("--yes", action="store_true", help="confirm cancellation")
    p_cancel.set_defaults(func=cmd_cancel)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
