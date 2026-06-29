"""Credential loading.

Credentials are read, in order of precedence, from:

1. Environment variables ``CM365_USERNAME`` / ``CM365_PASSWORD``.
2. A simple ``key=value`` file. The path can be set with ``CM365_CREDENTIALS``;
   otherwise these locations are tried in order:
   - ``./credentials.env`` (project-local, git-ignored)
   - ``~/.config/clubmanager365/credentials.env``

The file format is intentionally trivial::

    CM365_USERNAME=myuser
    CM365_PASSWORD=mypassword
    # optional, only needed once we know the club URL:
    CM365_BASE_URL=https://clubmanager365.com

Nothing here is ever printed or logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

DEFAULT_BASE_URL = "https://clubmanager365.com"

_SEARCH_PATHS = [
    Path.cwd() / "credentials.env",
    Path.home() / ".config" / "clubmanager365" / "credentials.env",
]


class ConfigError(Exception):
    """Raised when required credentials are missing."""


def _parse_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _load_file_values() -> Dict[str, str]:
    explicit = os.environ.get("CM365_CREDENTIALS")
    candidates = [Path(explicit)] if explicit else _SEARCH_PATHS
    for path in candidates:
        if path.is_file():
            return _parse_env_file(path)
    return {}


@dataclass
class Credentials:
    username: str
    password: str
    base_url: str = DEFAULT_BASE_URL


def load_credentials() -> Credentials:
    file_values = _load_file_values()

    def pick(key: str) -> Optional[str]:
        return os.environ.get(key) or file_values.get(key)

    username = pick("CM365_USERNAME")
    password = pick("CM365_PASSWORD")
    base_url = pick("CM365_BASE_URL") or DEFAULT_BASE_URL

    if not username or not password:
        raise ConfigError(
            "Missing credentials. Set CM365_USERNAME and CM365_PASSWORD as "
            "environment variables, or create a credentials.env file (see "
            "credentials.env.example)."
        )

    return Credentials(username=username, password=password, base_url=base_url.rstrip("/"))
