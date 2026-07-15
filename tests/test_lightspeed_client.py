"""Unit tests for src/client/lightspeed_client.py — mocked `requests` (design spec §3, §4.2, §4.3)."""

import unittest
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest import mock

import requests
from keboola.component.exceptions import UserException

from client.lightspeed_client import (
    _DEFAULT_RATE_LIMIT_SLEEP_SECONDS,
    _MAX_RATE_LIMIT_RETRIES,
    LightspeedXSeriesClient,
)
from configuration import AuthType


def _make_client(**overrides):
    kwargs = {
        "domain_prefix": "test-store",
        "api_version": "2026-07",
        "auth_type": AuthType.personal_token,
        "personal_token": "test-token",
    }
    kwargs.update(overrides)
    return LightspeedXSeriesClient(**kwargs)


def _response(status_code=200, json_data=None, headers=None, url="https://test-store.retail.lightspeed.app/api/2026-07/sales"):
    response = mock.Mock(spec=requests.Response)
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = json_data or {}
    response.url = url
    response.raise_for_status.side_effect = (
        requests.exceptions.HTTPError(f"{status_code} error") if status_code >= 400 else None
    )
    return response


class TestBaseUrlAndAuth(unittest.TestCase):
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_personal_token_uses_bearer_header(self, mock_get):
        mock_get.return_value = _response(json_data={"data": [], "version": {"max": 0}})
        client = _make_client()

        list(client.paginate("outlets", after=0, page_size=200))

        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_base_url_built_from_domain_prefix_and_api_version(self, mock_get):
        mock_get.return_value = _response(json_data={"data": [], "version": {"max": 0}})
        client = _make_client(domain_prefix="my-shop", api_version="2026-07")

        list(client.paginate("outlets", after=0, page_size=200))

        args, _ = mock_get.call_args
        self.assertEqual(args[0], "https://my-shop.retail.lightspeed.app/api/2026-07/outlets")


class TestPagination(unittest.TestCase):
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_stops_on_empty_data(self, mock_get):
        mock_get.return_value = _response(json_data={"data": [], "version": {"max": None}})
        client = _make_client()

        pages = list(client.paginate("sales", after=0, page_size=200))

        self.assertEqual(pages, [])
        mock_get.assert_called_once()

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_follows_version_cursor_verbatim(self, mock_get):
        page1 = _response(json_data={"data": [{"id": "1"}], "version": {"max": 137}})
        page2 = _response(json_data={"data": [], "version": {"max": None}})
        mock_get.side_effect = [page1, page2]
        client = _make_client()

        pages = list(client.paginate("sales", after=0, page_size=200))

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].version_max, 137)
        first_call_params = mock_get.call_args_list[0].kwargs["params"]
        second_call_params = mock_get.call_args_list[1].kwargs["params"]
        self.assertEqual(first_call_params["after"], 0)
        # Cursor passed back verbatim (never max + 1) — design spec §4.2.
        self.assertEqual(second_call_params["after"], 137)

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_stops_when_version_max_missing_even_with_data(self, mock_get):
        mock_get.return_value = _response(json_data={"data": [{"id": "1"}], "version": {}})
        client = _make_client()

        pages = list(client.paginate("sales", after=0, page_size=200))

        self.assertEqual(len(pages), 1)
        mock_get.assert_called_once()

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_multi_page_accumulation(self, mock_get):
        page1 = _response(json_data={"data": [{"id": "1"}], "version": {"max": 10}})
        page2 = _response(json_data={"data": [{"id": "2"}], "version": {"max": 20}})
        page3 = _response(json_data={"data": [], "version": {"max": None}})
        mock_get.side_effect = [page1, page2, page3]
        client = _make_client()

        pages = list(client.paginate("sales", after=0, page_size=200))

        self.assertEqual(len(pages), 2)
        self.assertEqual(mock_get.call_count, 3)


class TestErrorMapping(unittest.TestCase):
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_401_raises_user_exception(self, mock_get):
        mock_get.return_value = _response(status_code=401)
        client = _make_client()

        with self.assertRaises(UserException) as ctx:
            list(client.paginate("sales", after=0, page_size=200))
        self.assertIn("401", str(ctx.exception))

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_403_raises_user_exception(self, mock_get):
        mock_get.return_value = _response(status_code=403)
        client = _make_client()

        with self.assertRaises(UserException):
            list(client.paginate("sales", after=0, page_size=200))

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_404_raises_user_exception(self, mock_get):
        mock_get.return_value = _response(status_code=404)
        client = _make_client()

        with self.assertRaises(UserException) as ctx:
            list(client.paginate("sales", after=0, page_size=200))
        self.assertIn("404", str(ctx.exception))

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_other_4xx_5xx_raises_http_error(self, mock_get):
        mock_get.return_value = _response(status_code=500)
        client = _make_client()

        with self.assertRaises(requests.exceptions.HTTPError):
            list(client.paginate("sales", after=0, page_size=200))

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_timeout_raises_user_exception(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("timed out")
        client = _make_client()

        with self.assertRaises(UserException) as ctx:
            list(client.paginate("sales", after=0, page_size=200))
        self.assertIn("domain_prefix", str(ctx.exception))

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_connection_error_raises_user_exception(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("dns failure")
        client = _make_client()

        with self.assertRaises(UserException):
            list(client.paginate("sales", after=0, page_size=200))


class TestRateLimitBackoff(unittest.TestCase):
    @mock.patch("client.lightspeed_client.time.sleep")
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_retry_after_rfc1123_date_is_parsed_and_waited(self, mock_get, mock_sleep):
        retry_at = datetime.now(UTC) + timedelta(seconds=10)
        retry_after_header = format_datetime(retry_at, usegmt=True)
        rate_limited = _response(status_code=429, headers={"Retry-After": retry_after_header})
        success = _response(json_data={"data": [], "version": {"max": None}})
        mock_get.side_effect = [rate_limited, success]
        client = _make_client()

        list(client.paginate("sales", after=0, page_size=200))

        mock_sleep.assert_called_once()
        slept_seconds = mock_sleep.call_args[0][0]
        # ~10s remaining + 2s clock-skew guard, with some slack for test execution time.
        self.assertGreater(slept_seconds, 5)
        self.assertLess(slept_seconds, 15)

    @mock.patch("client.lightspeed_client.time.sleep")
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_missing_retry_after_uses_default_sleep(self, mock_get, mock_sleep):
        rate_limited = _response(status_code=429, headers={})
        success = _response(json_data={"data": [], "version": {"max": None}})
        mock_get.side_effect = [rate_limited, success]
        client = _make_client()

        list(client.paginate("sales", after=0, page_size=200))

        mock_sleep.assert_called_once_with(_DEFAULT_RATE_LIMIT_SLEEP_SECONDS)

    @mock.patch("client.lightspeed_client.time.sleep")
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_unparseable_retry_after_uses_default_sleep(self, mock_get, mock_sleep):
        rate_limited = _response(status_code=429, headers={"Retry-After": "not-a-date"})
        success = _response(json_data={"data": [], "version": {"max": None}})
        mock_get.side_effect = [rate_limited, success]
        client = _make_client()

        list(client.paginate("sales", after=0, page_size=200))

        mock_sleep.assert_called_once_with(_DEFAULT_RATE_LIMIT_SLEEP_SECONDS)

    @mock.patch("client.lightspeed_client.time.sleep")
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_resumes_from_last_cursor_after_rate_limit(self, mock_get, mock_sleep):
        rate_limited = _response(status_code=429, headers={})
        success = _response(json_data={"data": [{"id": "1"}], "version": {"max": 5}})
        done = _response(json_data={"data": [], "version": {"max": None}})
        mock_get.side_effect = [rate_limited, success, done]
        client = _make_client()

        pages = list(client.paginate("sales", after=0, page_size=200))

        self.assertEqual(len(pages), 1)
        # The retried call after 429 must reuse the same `after` cursor.
        first_attempt_params = mock_get.call_args_list[0].kwargs["params"]
        retried_params = mock_get.call_args_list[1].kwargs["params"]
        self.assertEqual(first_attempt_params["after"], retried_params["after"])

    @mock.patch("client.lightspeed_client.time.sleep")
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_bounded_429_cap_raises_user_exception(self, mock_get, mock_sleep):
        mock_get.return_value = _response(status_code=429, headers={})
        client = _make_client()

        with self.assertRaises(UserException) as ctx:
            list(client.paginate("sales", after=0, page_size=200))

        self.assertIn("429", str(ctx.exception))
        self.assertEqual(mock_sleep.call_count, _MAX_RATE_LIMIT_RETRIES)
        self.assertEqual(mock_get.call_count, _MAX_RATE_LIMIT_RETRIES + 1)

    @mock.patch("client.lightspeed_client.time.sleep")
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_rate_limit_retry_counter_resets_after_success(self, mock_get, mock_sleep):
        """A successful page in between shouldn't count towards the next endpoint's 429 cap."""
        rate_limited = _response(status_code=429, headers={})
        success = _response(json_data={"data": [{"id": "1"}], "version": {"max": 1}})
        mock_get.side_effect = [rate_limited, success, rate_limited, success, _response(json_data={"data": [], "version": {"max": None}})]
        client = _make_client()

        pages = list(client.paginate("sales", after=0, page_size=200))

        self.assertEqual(len(pages), 2)


class TestRateLimitPacing(unittest.TestCase):
    @mock.patch("client.lightspeed_client.time.sleep")
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_paces_when_remaining_quota_is_low(self, mock_get, mock_sleep):
        low_quota = _response(json_data={"data": [], "version": {"max": None}}, headers={"X-RateLimit-Remaining": "2"})
        mock_get.return_value = low_quota
        client = _make_client()

        list(client.paginate("sales", after=0, page_size=200))

        mock_sleep.assert_called_once()

    @mock.patch("client.lightspeed_client.time.sleep")
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_does_not_pace_when_quota_is_healthy(self, mock_get, mock_sleep):
        healthy_quota = _response(
            json_data={"data": [], "version": {"max": None}}, headers={"X-RateLimit-Remaining": "250"}
        )
        mock_get.return_value = healthy_quota
        client = _make_client()

        list(client.paginate("sales", after=0, page_size=200))

        mock_sleep.assert_not_called()


class TestConnectionCheck(unittest.TestCase):
    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_test_connection_calls_cheap_outlets_endpoint(self, mock_get):
        mock_get.return_value = _response(json_data={"data": [], "version": {"max": 0}})
        client = _make_client()

        client.test_connection()

        args, kwargs = mock_get.call_args
        self.assertTrue(args[0].endswith("/outlets"))
        self.assertEqual(kwargs["params"]["page_size"], 1)

    @mock.patch("client.lightspeed_client.requests.Session.get")
    def test_test_connection_propagates_auth_failure(self, mock_get):
        mock_get.return_value = _response(status_code=401)
        client = _make_client()

        with self.assertRaises(UserException):
            client.test_connection()


if __name__ == "__main__":
    unittest.main()
