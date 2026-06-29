"""HTTP client for clubmanager365.com (ASP.NET WebForms).

The site is a classic WebForms app: every page carries hidden ``__VIEWSTATE``,
``__VIEWSTATEGENERATOR`` and ``__EVENTVALIDATION`` fields that must be echoed
back on every POST ("postback"). This client keeps a ``requests.Session`` (so
cookies persist) and provides helpers to read a page, extract its form state,
and post back.

Login is a postback on the homepage login widget. The relevant fields were
identified from the live HTML:

    HeaderBarSection_NHP$LoginView5$UserLogin$UserName
    HeaderBarSection_NHP$LoginView5$UserLogin$Password
    HeaderBarSection_NHP$LoginView5$UserLogin$LoginSubmitButton
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

from .config import Credentials

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Login widget field names (WebForms uses $ in name, _ in id).
LOGIN_FIELD_PREFIX = "HeaderBarSection_NHP$LoginView5$UserLogin"
LOGIN_USERNAME = f"{LOGIN_FIELD_PREFIX}$UserName"
LOGIN_PASSWORD = f"{LOGIN_FIELD_PREFIX}$Password"
LOGIN_SUBMIT = f"{LOGIN_FIELD_PREFIX}$LoginSubmitButton"

# Hidden fields that make up a WebForms "page state".
STATE_FIELDS = (
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTVALIDATION",
    "__VIEWSTATEENCRYPTED",
    "__PREVIOUSPAGE",
)


class LoginError(Exception):
    """Raised when a login attempt fails."""


class ActionError(Exception):
    """Raised when an ActionHandler.ashx call fails or returns an error page."""


@dataclass
class Page:
    """A fetched page plus its parsed form state."""

    url: str
    status_code: int
    html: str
    soup: BeautifulSoup = field(repr=False)

    def form_state(self, form_id: str = "aspnetForm") -> Dict[str, str]:
        """Collect every hidden input value from a form (the postback state)."""
        form = self.soup.find("form", id=form_id) or self.soup.find("form")
        state: Dict[str, str] = {}
        if form is None:
            return state
        for inp in form.find_all("input", attrs={"type": "hidden"}):
            name = inp.get("name")
            if name:
                state[name] = inp.get("value", "")
        return state

    @property
    def title(self) -> str:
        t = self.soup.find("title")
        return t.get_text(strip=True) if t else ""


class ClubManagerClient:
    """A logged-in (or about to be) session against clubmanager365.com."""

    def __init__(self, credentials: Credentials, timeout: int = 30):
        self.creds = credentials
        self.base_url = credentials.base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._logged_in = False

    # -- low level -------------------------------------------------------

    def _abs(self, path_or_url: str) -> str:
        if path_or_url.startswith("http"):
            return path_or_url
        return urljoin(self.base_url + "/", path_or_url.lstrip("/"))

    def get(self, path_or_url: str) -> Page:
        url = self._abs(path_or_url)
        resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        return self._to_page(resp)

    def post(self, path_or_url: str, data: Dict[str, str]) -> Page:
        url = self._abs(path_or_url)
        resp = self.session.post(
            url, data=data, timeout=self.timeout, allow_redirects=True
        )
        return self._to_page(resp)

    @staticmethod
    def _to_page(resp: requests.Response) -> Page:
        soup = BeautifulSoup(resp.text, "html.parser")
        return Page(
            url=resp.url,
            status_code=resp.status_code,
            html=resp.text,
            soup=soup,
        )

    # -- auth ------------------------------------------------------------

    def login(self) -> Page:
        """Perform the WebForms login postback. Returns the post-login page."""
        home = self.get("Homepage.aspx")
        state = home.form_state()
        if "__VIEWSTATE" not in state:
            raise LoginError(
                "Could not find the login form / __VIEWSTATE on the homepage. "
                "The site layout may have changed."
            )

        payload: Dict[str, str] = dict(state)
        payload["__EVENTTARGET"] = ""
        payload["__EVENTARGUMENT"] = ""
        payload[LOGIN_USERNAME] = self.creds.username
        payload[LOGIN_PASSWORD] = self.creds.password
        payload[LOGIN_SUBMIT] = "Login"

        result = self.post("Homepage.aspx", payload)

        if not self._looks_logged_in(result):
            raise LoginError(
                "Login failed — still seeing the login form after submitting. "
                "Check CM365_USERNAME / CM365_PASSWORD."
            )
        self._logged_in = True
        return result

    def _looks_logged_in(self, page: Page) -> bool:
        """Heuristic: the login widget disappears once authenticated.

        WebForms ``<asp:LoginView>`` swaps templates after auth, so the
        UserName input vanishes. We treat its absence as success.
        """
        return page.soup.find("input", attrs={"name": LOGIN_USERNAME}) is None

    # -- ActionHandler AJAX API -----------------------------------------

    def action(
        self,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        callback: str = "CourtCallback",
    ) -> Any:
        """Call ``/Club/ActionHandler.ashx`` and return the decoded JSON.

        The site's jQuery uses GET with the request object **serialised as a
        JSON string appended to the query string** (not a POST body). The
        server reads it from the raw query string, so we must reproduce that
        exactly. A POST with a JSON body silently fails into an error page.
        """
        payload = payload or {}
        ts = int(time.time() * 1000)
        json_str = json.dumps(payload, separators=(",", ":"))
        # Match the browser: keep structural chars literal, encode the rest.
        encoded = quote(json_str, safe='{}[]":,')
        url = (
            f"{self.base_url}/Club/ActionHandler.ashx"
            f"?siteCallback={callback}&action={action}&_={ts}&{encoded}"
        )
        resp = self.session.get(
            url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": f"{self.base_url}/Club/Bookings.aspx",
            },
            timeout=self.timeout,
        )
        if "Error Page" in resp.text[:300] or "<!DOCTYPE" in resp.text[:50]:
            raise ActionError(
                f"ActionHandler '{action}' returned an error page "
                f"(status {resp.status_code}). Payload may be invalid or the "
                f"session expired."
            )
        try:
            return resp.json()
        except ValueError as exc:  # pragma: no cover - defensive
            raise ActionError(
                f"ActionHandler '{action}' did not return JSON: {exc}"
            ) from exc
