"""HTTP client for the Lightspeed Retail X-Series REST API.

The only module in the component that talks HTTP. Builds the per-store base
URL, attaches auth, follows the `version`-cursor pagination and handles
rate-limit backoff (design spec §3 Authentication, §4.2 Pagination,
§4.3 Rate limits).
"""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests
from keboola.component.exceptions import UserException

from configuration import AuthType

_DEFAULT_TIMEOUT_SECONDS = 60
# Slightly above the 5-minute rolling rate-limit window (design spec §4.3).
_MAX_RATE_LIMIT_SLEEP_SECONDS = 320
# Fallback wait when `Retry-After` is missing or not a parsable RFC1123 date.
_DEFAULT_RATE_LIMIT_SLEEP_SECONDS = 30
_CLOCK_SKEW_GUARD_SECONDS = 2
_RATE_LIMIT_PACE_THRESHOLD = 5
_RATE_LIMIT_PACE_SLEEP_SECONDS = 2
# Versioning exception (design spec §3.3): the OAuth token endpoint stays on 1.0.
_OAUTH_TOKEN_API_VERSION = "1.0"
# Cap on consecutive 429 responses before giving up (design spec §4.3 is a rolling
# window, not an infinite backoff contract).
_MAX_RATE_LIMIT_RETRIES = 10


@dataclass(frozen=True)
class Page:
    """One page of a paginated collection response."""

    records: list[dict]
    version_max: int | None


class LightspeedXSeriesClient:
    """Thin REST client for a single X-Series store."""

    def __init__(
        self,
        domain_prefix: str,
        api_version: str,
        auth_type: AuthType,
        personal_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        refresh_token: str = "",
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._domain_prefix = domain_prefix
        self._api_version = api_version
        self._auth_type = auth_type
        self._personal_token = personal_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._timeout = timeout
        self._session = requests.Session()
        self._oauth_access_token: str | None = None

    def test_connection(self) -> None:
        """Cheap connectivity check used by the `testConnection` sync action (design spec §2.5)."""
        self._get("outlets", params={"page_size": 1})

    def paginate(self, endpoint: str, after: int, page_size: int) -> Iterator[Page]:
        """Yield pages of `endpoint`, following the `version` cursor until `data` is empty.

        The returned `version.max` is passed back verbatim as the next page's `after` —
        never `max + 1`, because the cursor is not guaranteed to increment by 1
        (design spec §4.2).
        """
        cursor = after
        while True:
            payload = self._get(endpoint, params={"after": cursor, "page_size": page_size})
            records = payload.get("data") or []
            if not records:
                return
            version_max = (payload.get("version") or {}).get("max")
            yield Page(records=records, version_max=version_max)
            if version_max is None:
                return
            cursor = version_max

    def _get(self, endpoint: str, params: dict) -> dict:
        url = f"{self._base_url()}/{endpoint}"
        rate_limit_retries = 0
        while True:
            try:
                response = self._session.get(url, params=params, headers=self._request_headers(), timeout=self._timeout)
            except requests.exceptions.RequestException as exc:
                raise UserException(
                    f"Could not connect to '{url}'. Check the 'domain_prefix' and network connectivity."
                ) from exc

            if response.status_code == 429:
                rate_limit_retries += 1
                if rate_limit_retries > _MAX_RATE_LIMIT_RETRIES:
                    raise UserException(
                        f"'{url}' is persistently rate-limited (429) after {_MAX_RATE_LIMIT_RETRIES} "
                        "consecutive retries. The store may be under heavy load; try again later."
                    )
                self._wait_for_rate_limit_reset(response)
                continue

            rate_limit_retries = 0
            self._raise_for_known_errors(response)
            self._pace_near_rate_limit(response)
            return response.json()

    def _base_url(self, api_version: str | None = None) -> str:
        return f"https://{self._domain_prefix}.retail.lightspeed.app/api/{api_version or self._api_version}"

    def _request_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _access_token(self) -> str:
        if self._auth_type == AuthType.personal_token:
            return self._personal_token
        return self._oauth_access_token or self._fetch_oauth_access_token()

    def _fetch_oauth_access_token(self) -> str:
        """Exchange the configured refresh token for an access token.

        Structurally supported per design spec §3.3/§3.4 but not activated in v1:
        every token response rotates the refresh token, and using it revokes the
        previous access token. Persisting that rotation safely needs Keboola's
        OAuth broker (or forcing a single sequential row) rather than per-row
        state — out of scope until OAuth is switched on for this component.
        """
        url = f"{self._base_url(_OAUTH_TOKEN_API_VERSION)}/token"
        try:
            response = self._session.post(
                url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise UserException(f"Could not reach the OAuth token endpoint '{url}'.") from exc
        self._raise_for_known_errors(response)
        token_payload = response.json()
        self._oauth_access_token = token_payload["access_token"]
        return self._oauth_access_token

    @staticmethod
    def _raise_for_known_errors(response: requests.Response) -> None:
        if response.status_code in (401, 403):
            raise UserException(
                f"Authentication failed ({response.status_code}). Check the credentials and 'domain_prefix'."
            )
        if response.status_code == 404:
            raise UserException(
                f"'{response.url}' was not found (404). Check the 'domain_prefix', 'api_version' and object."
            )
        if response.status_code >= 400:
            response.raise_for_status()

    def _wait_for_rate_limit_reset(self, response: requests.Response) -> None:
        sleep_seconds = self._rate_limit_sleep_seconds(response)
        logging.warning(
            "Rate limit exceeded (429). Waiting %.0f seconds for the 'Retry-After' window before resuming.",
            sleep_seconds,
        )
        time.sleep(sleep_seconds)

    @staticmethod
    def _rate_limit_sleep_seconds(response: requests.Response) -> float:
        """`Retry-After` is an RFC1123 HTTP-date (absolute timestamp), not seconds (design spec §4.3)."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                remaining = (retry_at - datetime.now(UTC)).total_seconds()
                remaining += _CLOCK_SKEW_GUARD_SECONDS
                return max(0.0, min(remaining, _MAX_RATE_LIMIT_SLEEP_SECONDS))
            except (TypeError, ValueError):
                logging.warning("Could not parse 'Retry-After' header %r as an RFC1123 date.", retry_after)
        return _DEFAULT_RATE_LIMIT_SLEEP_SECONDS

    @staticmethod
    def _pace_near_rate_limit(response: requests.Response) -> None:
        """Proactively slow down when `X-RateLimit-Remaining` is nearly exhausted."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None and remaining.isdigit() and int(remaining) <= _RATE_LIMIT_PACE_THRESHOLD:
            logging.info("X-RateLimit-Remaining is low (%s); pacing requests to avoid a 429.", remaining)
            time.sleep(_RATE_LIMIT_PACE_SLEEP_SECONDS)
