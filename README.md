# clubmanager365cli

[![PyPI version](https://img.shields.io/pypi/v/clubmanager365cli.svg)](https://pypi.org/project/clubmanager365cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/clubmanager365cli.svg)](https://pypi.org/project/clubmanager365cli/)
[![License](https://img.shields.io/pypi/l/clubmanager365cli.svg)](LICENSE)

A command-line tool to log in to [clubmanager365.com](https://clubmanager365.com)
and book courts from the terminal — and an **MCP server** exposing the same
actions so AI agents can book courts for you.

> Personal automation for your own account. Use responsibly and within your
> club's terms of use.

## Booking rules

Each club configures its own booking rules, so your club may differ. By default
this tool assumes the same rules as the club it was developed against:

- Every booking **requires at least one opponent** (`book --with <name|id>`).
- **One slot per person per day** — booking a day you already have fails.
- Booking needs no payment at the time of booking (covered by membership /
  court credits), so there's no checkout step.

## Install

The quickest way — run the CLI without installing anything, via
[uv](https://docs.astral.sh/uv/) (it fetches the package on demand):

```bash
uvx --from clubmanager365cli cm365 --help
```

Or install it as a persistent global `cm365` command with
[pipx](https://pipx.pypa.io/):

```bash
pipx install clubmanager365cli
cm365 --help
```

Or from source, for development:

```bash
git clone https://github.com/jerry-shao/clubmanager365cli
cd clubmanager365cli
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Credentials

Provide your credentials (kept local, git-ignored):

```bash
cp credentials.env.example credentials.env
```

Then open `credentials.env` in an editor and fill in your username and password.

Alternatively, export them as environment variables (quote both values —
usernames and passwords can contain spaces):

```bash
export CM365_USERNAME="your username"
export CM365_PASSWORD="your password"
```

If your club's match type differs from the default (`4` = Friendly), set
`CM365_MATCH_TYPE` too — run `cm365 match-types` to find your club's ids. This
applies to both the CLI and the MCP server.

## Usage

The examples below assume `cm365` is on your PATH (pipx or source install). If
you use `uvx`, prefix each command with `uvx --from clubmanager365cli`.

```bash
cm365 login                       # verify your credentials work
cm365 mybookings                  # list your upcoming bookings
cm365 slots tomorrow -a           # free slots tomorrow
cm365 slots 2026-07-04 -t 18:00   # all courts at 18:00 on a date
cm365 slots today --type indoor   # restrict to indoor courts
cm365 players "pat smith"        # find an opponent's id by name (quote names with spaces)
cm365 match-types                # list your club's match-type ids (e.g. Friendly)

# Booking requires an opponent and is a dry run unless you pass --yes:
cm365 book tomorrow 18:00 --with "Pat Smith"             # dry run (finds slot)
cm365 book tomorrow 18:00 -c "Indoor Court 1" --with "Pat Smith" --yes
cm365 book tomorrow 18:00 --with 100001 --with 100002 --yes   # doubles, by id

cm365 cancel 12345678 --yes       # cancel a booking by id (from mybookings)
```

Dates accept `today`, `tomorrow`, `YYYY-MM-DD`, or `27 Jun 2026`.
Times accept `18`, `18:00`, or `6pm`. Opponents accept names (fuzzy) or ids.

### Diagnostics

```bash
cm365 whoami             # post-login landing page + nav links
cm365 explore [PATH] -o page.local.html   # dump a page's HTML/forms/links
```

## MCP server

The same actions are exposed as an [MCP](https://modelcontextprotocol.io)
server so an AI assistant (Claude Desktop, Claude Code, …) can book courts for
you. It runs **locally over stdio with your own credentials** — there is no
shared/hosted server, so your login never leaves your machine.

Tools: `list_slots`, `my_bookings`, `search_players`, `list_match_types`,
`book_court`, `cancel_booking`. `book_court` and `cancel_booking` are a **dry
run unless you pass `confirm: true`**, so the assistant can't book or cancel
without an explicit confirmation step.

### Run it

No manual install needed — [`uv`](https://docs.astral.sh/uv/) runs it (and
provisions a suitable Python; the MCP SDK needs 3.10+):

```bash
uvx --from "clubmanager365cli[mcp]" clubmanager365-mcp
```

### Connect a client

All clients need the same three things: `command: uvx`, the `args` below, and
your credentials in `env`. A few examples; other MCP clients follow the same
pattern.

**OpenClaw** — add with the CLI:

```bash
openclaw mcp add clubmanager365 \
  --command uvx \
  --arg --from --arg "clubmanager365cli[mcp]" --arg clubmanager365-mcp \
  --env CM365_USERNAME=your-username \
  --env CM365_PASSWORD=your-password
```

or edit `~/.openclaw/openclaw.json` directly (servers live under `mcp.servers`):

```json
{
  "mcp": {
    "servers": {
      "clubmanager365": {
        "command": "uvx",
        "args": ["--from", "clubmanager365cli[mcp]", "clubmanager365-mcp"],
        "env": {
          "CM365_USERNAME": "your-username",
          "CM365_PASSWORD": "your-password"
        }
      }
    }
  }
}
```

Verify with `openclaw mcp doctor clubmanager365 --probe`.

**Hermes Agent** — add to `~/.hermes/hermes-agent/config.yaml` (top-level key
`mcp_servers`), then run `/reload-mcp`:

```yaml
mcp_servers:
  clubmanager365:
    command: "uvx"
    args: ["--from", "clubmanager365cli[mcp]", "clubmanager365-mcp"]
    env:
      CM365_USERNAME: "your-username"
      CM365_PASSWORD: "your-password"
```

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "clubmanager365": {
      "command": "uvx",
      "args": ["--from", "clubmanager365cli[mcp]", "clubmanager365-mcp"],
      "env": {
        "CM365_USERNAME": "your-username",
        "CM365_PASSWORD": "your-password"
      }
    }
  }
}
```

**Codex CLI** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.clubmanager365]
command = "uvx"
args = ["--from", "clubmanager365cli[mcp]", "clubmanager365-mcp"]
env = { CM365_USERNAME = "your-username", CM365_PASSWORD = "your-password" }
```

## The booking API

All booking actions go through `/Club/ActionHandler.ashx`, with the request
object serialised as a JSON string carried on the request, matching the calls
the site's own front-end makes:

```
/Club/ActionHandler.ashx?siteCallback=CourtCallback&action=GetCourtDay&_=<ts>&{"Date":"27 Jun 2026",...}
```

When a booking takes no payment at booking time (e.g. it's covered by
membership or court credits), booking is a **single `MakeBooking` call** — no
preliminary hold and no `BookingPlayerID` are needed. `GetCourtDay` lists the
slots (each cell carries a `CourtSlotID`; each column a `CourtID`), and
`MakeBooking` takes `CourtsRequired: [{c: CourtID, s: CourtSlotID}]` plus
`OpponentPlayerIDs`, `SelectedMatchType`, `MatchDate`, etc. Clubs that take
payment instead go through `SaveNewPreliminaryBooking` (a short-lived hold)
before confirming — that path isn't exercised here.
See [`cm365/bookings.py`](cm365/bookings.py).

## How login works

The site is ASP.NET WebForms. Logging in is a "postback" on the homepage:

1. `GET /Homepage.aspx` → session cookie + hidden `__VIEWSTATE`,
   `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`.
2. `POST /Homepage.aspx` echoing those hidden fields plus
   `…UserLogin$UserName`, `…UserLogin$Password`, `…UserLogin$LoginSubmitButton`.
3. The `<asp:LoginView>` widget swaps to its authenticated template; cookies
   now carry the session.

See [`cm365/client.py`](cm365/client.py).
