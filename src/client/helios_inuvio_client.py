import logging
from collections.abc import Iterable, Iterator
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Endpoints/shapes confirmed against the sandbox — see docs/superpowers/helios-api-notes.md
TOKEN_PATH = "/oauth/token"
TOKEN_FIELD = "access_token"

# Plain-language, user-safe messages keyed by HTTP status. The raw response body and the
# internal request path are kept out of these (they go to logging.debug only), because the
# message is surfaced verbatim to the user via UserException.
_STATUS_MESSAGES = {
    400: "Helios iNuvio rejected the request (HTTP 400). Check the field mapping and values.",
    401: "Authentication or permission failed (HTTP 401).",
    403: "Authentication or permission failed (HTTP 403).",
    404: "The requested browse or record was not found (HTTP 404).",
    422: "Helios iNuvio rejected the record (HTTP 422). Check the field mapping and values.",
}

# Statuses that mean "this individual record was rejected" — they are per-row data problems,
# so continue_on_error can legitimately skip the row and carry on. Every other failure
# (transport loss, 401/403 auth, 404 wrong browse, 429 rate limit, 5xx) is fatal to the whole
# batch: re-running every remaining row would just reproduce it, so the run must abort instead
# of filling failed_records.csv with identical infrastructure errors.
_DATA_REJECTION_STATUSES = frozenset({400, 409, 422})


class HeliosInuvioClientError(Exception):
    """Client-side error. `detail` carries the raw API response body for the failed_records
    CSV; the message itself stays user-safe so it can flow into a UserException. `fatal` marks
    an error that must abort the whole batch even under continue_on_error (transport/auth/5xx),
    as opposed to a per-row data rejection that continue_on_error may skip."""

    def __init__(self, message: str, detail: str | None = None, fatal: bool = False):
        super().__init__(message)
        self.detail = detail
        self.fatal = fatal


class HeliosInuvioClient:
    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        database: str,
        timeout: int = 60,
    ):
        self._base_url = server.rstrip("/")
        self._username = username
        self._password = password
        self._database = database
        self._timeout = timeout
        self._token: str | None = None
        self._session = self._build_session()

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        # Retries are limited to GET. POST/PUT are writes with no idempotency key, so a write the
        # server committed before answering with a 5xx (or dropping the connection) would be
        # re-sent and duplicated. Write failures instead flow to the continue_on_error path.
        retry = Retry(
            total=5,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self._base_url}{path}"
        try:
            response = self._session.request(method, url, timeout=self._timeout, **kwargs)
        except requests.RequestException as exc:
            # Transport loss (connection drop, timeout, exhausted retries) is never a per-row
            # problem — abort the batch rather than recording every remaining row as "failed".
            raise HeliosInuvioClientError(
                f"Could not reach Helios iNuvio at {self._base_url}: {exc}", fatal=True
            ) from exc
        if not response.ok:
            if path == TOKEN_PATH:
                # Never log the token-endpoint body: it can echo the submitted credentials.
                logging.debug("Auth error: HTTP %s", response.status_code)
                raise HeliosInuvioClientError(f"Authentication failed (HTTP {response.status_code}).", fatal=True)
            logging.debug("Helios API error: %s %s -> %s: %s", method, path, response.status_code, response.text[:500])
            message = _STATUS_MESSAGES.get(
                response.status_code, f"Helios iNuvio request failed (HTTP {response.status_code})."
            )
            fatal = response.status_code not in _DATA_REJECTION_STATUSES
            raise HeliosInuvioClientError(message, detail=response.text[:500], fatal=fatal)
        return response

    @staticmethod
    def _json(response: requests.Response) -> Any:
        # A 2xx with an empty body is treated as an empty object (a successful write may return
        # no content). A non-empty, non-JSON body (HTML error page, captive portal, truncated
        # response) makes requests raise JSONDecodeError (a ValueError); convert it to a
        # HeliosInuvioClientError so the component layer turns it into a UserException (exit 1)
        # rather than an uncaught exit 2.
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise HeliosInuvioClientError(
                f"Helios iNuvio returned a non-JSON response (HTTP {response.status_code})."
            ) from exc

    def authenticate(self) -> None:
        # `database` is deliberately absent here: it is not an auth param but a path
        # segment ({dbName}) on every System API call (see self._database below).
        payload = {
            "grant_type": "password",
            "username": self._username,
            "password": self._password,
            # The component only ever runs headless on Linux; SQL-user auth is the only mode.
            "windowsAuthentication": "false",
        }
        response = self._request(
            "POST",
            TOKEN_PATH,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token = self._json(response).get(TOKEN_FIELD)
        if not token:
            raise HeliosInuvioClientError("Authentication succeeded but no token was returned.")
        self._token = token
        self._session.headers["Authorization"] = f"Bearer {token}"
        logging.info("Authenticated against Helios iNuvio.")

    def list_browses(self) -> list[dict[str, Any]]:
        response = self._request("GET", f"/api/v1/browses/list/{self._database}")
        browses = []
        for b in self._json(response).get("browses", []):  # response is {count, browses:[...]}
            can_create = b.get("browseCreateImplemented") and b.get("browseUserRightCreate")
            can_update = b.get("browseUpdateImplemented") and b.get("browseUserRightUpdate")
            if not (can_create or can_update):
                continue
            ident = b.get("browseIdent")
            if ident is None:
                raise HeliosInuvioClientError("Unexpected response from Helios iNuvio: a browse is missing its id.")
            browses.append({"name": str(ident), "label": b.get("browseCaption")})
        return browses

    def get_writable_fields(self, browse: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/api/v1/browses/meta/{self._database}/{browse}")
        fields = self._json(response).get("table", {}).get("tableFields", [])
        result = []
        for f in fields:
            if f.get("fieldDisabledForCreate"):
                continue
            sys_name = f.get("fieldSysName")
            if not sys_name:
                raise HeliosInuvioClientError("Unexpected response from Helios iNuvio: a field is missing its name.")
            result.append(
                {
                    "field_name": sys_name,
                    "label": f.get("fieldShortDisplayName") or f.get("fieldDisplayName") or sys_name,
                    "type": f.get("fieldType"),
                }
            )
        return result

    def write_record(self, browse: str, mode: str, key_field: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        base = f"/api/v1/browses/data/{self._database}/{browse}"
        if mode == "update":
            if not key_field:
                raise HeliosInuvioClientError("Update mode requires a key_field.")
            record_id = row.pop(key_field, None)
            if record_id in (None, ""):
                raise HeliosInuvioClientError(f"Row is missing a value for key field '{key_field}'.")
            response = self._request("PUT", f"{base}/{record_id}", json={"row": row})
        else:
            response = self._request("POST", base, json={"row": row})
        return self._json(response)

    def write_batch(
        self,
        browse: str,
        mode: str,
        key_field: str | None,
        rows: Iterable[dict[str, Any]],
        continue_on_error: bool,
    ) -> Iterator[dict[str, Any] | None]:
        """Write rows one at a time, yielding one item per row as it is processed: None for a
        success, or a {"row", "error"} dict for a rejected row. Yielding (rather than buffering
        and returning) lets the caller persist each failure immediately, so a mid-batch crash
        does not lose the failures collected so far. Raises when continue_on_error is False, and
        always raises on a fatal error (transport/auth/5xx) so an outage is not silently logged
        as a CSV full of per-row failures."""
        for row in rows:
            try:
                self.write_record(browse, mode, key_field, row)
                yield None
            except HeliosInuvioClientError as exc:
                if exc.fatal or not continue_on_error:
                    raise
                yield {"row": row, "error": exc.detail or str(exc)}
