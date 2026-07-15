"""Unit tests for src/configuration.py (design spec §5.3)."""

import unittest

from keboola.component.exceptions import UserException

from configuration import DEFAULT_API_VERSION, DEFAULT_PAGE_SIZE, AuthType, Configuration, LoadType, ObjectName


def _base_params(**overrides):
    params = {
        "domain_prefix": "test-store",
        "#personal_token": "secret-token-value",
    }
    params.update(overrides)
    return params


class TestDefaults(unittest.TestCase):
    def test_minimal_personal_token_config_succeeds(self):
        config = Configuration(**_base_params())
        self.assertEqual(config.domain_prefix, "test-store")
        self.assertEqual(config.api_version, DEFAULT_API_VERSION)
        self.assertEqual(config.auth_type, AuthType.personal_token)
        self.assertEqual(config.page_size, DEFAULT_PAGE_SIZE)
        self.assertEqual(config.load_type, LoadType.incremental_load)
        self.assertIsNone(config.object)

    def test_personal_token_alias_populates_field(self):
        config = Configuration(**_base_params(**{"#personal_token": "abc123"}))
        self.assertEqual(config.personal_token, "abc123")

    def test_client_secret_and_refresh_token_aliases(self):
        config = Configuration(
            domain_prefix="test-store",
            auth_type=AuthType.oauth,
            client_id="client-1",
            **{"#client_secret": "secret-1", "#refresh_token": "refresh-1"},
        )
        self.assertEqual(config.client_secret, "secret-1")
        self.assertEqual(config.refresh_token, "refresh-1")

    def test_extra_unknown_fields_are_ignored(self):
        config = Configuration(**_base_params(some_future_field="ignored", debug=True))
        self.assertEqual(config.domain_prefix, "test-store")


class TestAuthCrossFieldValidation(unittest.TestCase):
    def test_personal_token_mode_without_token_raises_user_exception(self):
        with self.assertRaises(UserException) as ctx:
            Configuration(domain_prefix="test-store")
        self.assertIn("#personal_token", str(ctx.exception))

    def test_personal_token_mode_with_empty_token_raises_user_exception(self):
        with self.assertRaises(UserException):
            Configuration(domain_prefix="test-store", **{"#personal_token": ""})

    def test_oauth_mode_missing_all_fields_raises_user_exception(self):
        with self.assertRaises(UserException) as ctx:
            Configuration(domain_prefix="test-store", auth_type=AuthType.oauth)
        self.assertIn("client_id", str(ctx.exception))

    def test_oauth_mode_missing_one_field_raises_user_exception(self):
        with self.assertRaises(UserException):
            Configuration(
                domain_prefix="test-store",
                auth_type=AuthType.oauth,
                client_id="client-1",
                **{"#client_secret": "secret-1"},
                # missing #refresh_token
            )

    def test_oauth_mode_with_all_fields_succeeds(self):
        config = Configuration(
            domain_prefix="test-store",
            auth_type=AuthType.oauth,
            client_id="client-1",
            **{"#client_secret": "secret-1", "#refresh_token": "refresh-1"},
        )
        self.assertEqual(config.auth_type, AuthType.oauth)

    def test_error_message_combines_multiple_field_level_errors(self):
        # Two independent single-field errors are collected into one combined message.
        with self.assertRaises(UserException) as ctx:
            Configuration(auth_type="not_a_real_auth_type", page_size="not-an-int")
        message = str(ctx.exception)
        self.assertIn("domain_prefix", message)
        self.assertIn("auth_type", message)
        self.assertIn("page_size", message)

    def test_missing_required_field_does_not_reach_cross_field_validator(self):
        # A missing required field short-circuits pydantic before model_validator(mode="after")
        # runs, so the message reports only the field-level error, not the auth cross-field one.
        with self.assertRaises(UserException) as ctx:
            Configuration()
        message = str(ctx.exception)
        self.assertIn("domain_prefix", message)
        self.assertNotIn("#personal_token", message)


class TestPageSizeValidation(unittest.TestCase):
    def test_zero_page_size_raises_user_exception(self):
        with self.assertRaises(UserException):
            Configuration(**_base_params(page_size=0))

    def test_negative_page_size_raises_user_exception(self):
        with self.assertRaises(UserException):
            Configuration(**_base_params(page_size=-10))

    def test_positive_page_size_succeeds(self):
        config = Configuration(**_base_params(page_size=50))
        self.assertEqual(config.page_size, 50)


class TestIncrementalComputedField(unittest.TestCase):
    def test_incremental_load_type_is_incremental_true(self):
        config = Configuration(**_base_params(load_type=LoadType.incremental_load))
        self.assertTrue(config.incremental)

    def test_full_load_type_is_incremental_false(self):
        config = Configuration(**_base_params(load_type=LoadType.full_load))
        self.assertFalse(config.incremental)

    def test_default_load_type_is_incremental(self):
        config = Configuration(**_base_params())
        self.assertTrue(config.incremental)


class TestObjectField(unittest.TestCase):
    def test_object_defaults_to_none_for_sync_actions(self):
        config = Configuration(**_base_params())
        self.assertIsNone(config.object)

    def test_object_accepts_known_stream_name(self):
        config = Configuration(**_base_params(object=ObjectName.sales))
        self.assertEqual(config.object, ObjectName.sales)

    def test_object_rejects_unknown_stream_name(self):
        with self.assertRaises(UserException):
            Configuration(**_base_params(object="not_a_real_stream"))


if __name__ == "__main__":
    unittest.main()
