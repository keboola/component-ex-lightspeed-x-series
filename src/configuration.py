"""Pydantic configuration model for keboola.ex-lightspeed-x-series.

The component always receives a single, already-merged `parameters` dict —
root config (connection/auth) merged with the row config (object/load
settings) by the platform (see design spec §5, keboola-context/config-rows.md).
One model covers both.
"""

from enum import StrEnum

from keboola.component.exceptions import UserException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field, model_validator

DEFAULT_API_VERSION = "2026-07"
DEFAULT_PAGE_SIZE = 200


class AuthType(StrEnum):
    personal_token = "personal_token"
    oauth = "oauth"


class ObjectName(StrEnum):
    """The v1 stream set (design spec §4.1). Matches `streams.STREAM_REGISTRY`."""

    sales = "sales"
    products = "products"
    inventory = "inventory"
    customers = "customers"
    outlets = "outlets"
    registers = "registers"
    payment_types = "payment_types"


class LoadType(StrEnum):
    full_load = "full_load"
    incremental_load = "incremental_load"


class Configuration(BaseModel):
    """Root ∪ row configuration, as the platform delivers it at runtime."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # --- connection (root config) ---------------------------------------
    domain_prefix: str
    api_version: str = DEFAULT_API_VERSION
    auth_type: AuthType = AuthType.personal_token

    personal_token: str = Field(default="", alias="#personal_token")
    client_id: str = ""
    client_secret: str = Field(default="", alias="#client_secret")
    refresh_token: str = Field(default="", alias="#refresh_token")

    # --- per-object (row config) -----------------------------------------
    # `object` is only needed by `run()`, not by the `testConnection` sync action, so it
    # must default to None rather than being a required field (see keboola-context/
    # configuration-model.md "sync action gotcha") — validated explicitly in `run()`.
    object: ObjectName | None = None
    load_type: LoadType = LoadType.incremental_load
    page_size: int = DEFAULT_PAGE_SIZE

    def __init__(self, **data):
        try:
            super().__init__(**data)
        except ValidationError as e:
            # `err["loc"]` is empty for cross-field (`model_validator`) errors, only
            # populated for single-field errors — handle both.
            error_messages = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}".lstrip(": ") for err in e.errors()]
            raise UserException(f"Validation Error: {', '.join(error_messages)}") from e

    @computed_field
    @property
    def incremental(self) -> bool:
        return self.load_type == LoadType.incremental_load

    @model_validator(mode="after")
    def _validate_auth_fields(self) -> Configuration:
        if self.auth_type == AuthType.personal_token and not self.personal_token:
            raise ValueError("'#personal_token' is required when auth_type is 'personal_token'.")
        if self.auth_type == AuthType.oauth and not (self.client_id and self.client_secret and self.refresh_token):
            raise ValueError(
                "'client_id', '#client_secret' and '#refresh_token' are all required when auth_type is 'oauth'."
            )
        return self

    @model_validator(mode="after")
    def _validate_page_size(self) -> Configuration:
        if self.page_size <= 0:
            raise ValueError("'page_size' must be a positive integer.")
        return self
