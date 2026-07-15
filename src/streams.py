"""Stream-definition registry for keboola.ex-lightspeed-x-series.

Declares, per source object (design spec §4.1), the collection endpoint, the
parent table's primary key and explicit column schema (native `base.type`s
for the authoritative manifest, design spec §6), and the nested arrays
flattened into child tables (design spec §4.4). Adding one of the deferred
streams (§4.1 "explicitly deferred") later means adding one entry here —
no other module needs to change.

Column sets are a best-effort mapping of the Lightspeed Retail X-Series
response shapes (design spec / https://x-series-api.lightspeedhq.com/reference/*)
made without live API access; §9 risks #3 and #6 call out that they are
verified/adjusted against real payloads once VCR cassettes are recorded
(Phase 5) — this registry is the single place that fixes.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from keboola.component.dao import BaseType, ColumnDefinition

from configuration import ObjectName

ColumnSchema = Mapping[str, ColumnDefinition]


def _pk() -> ColumnDefinition:
    """A required string primary-key column."""
    return ColumnDefinition(data_types=BaseType.string(), primary_key=True, nullable=False)


def _fk() -> ColumnDefinition:
    """A required string foreign-key column (injected by the flattener)."""
    return ColumnDefinition(data_types=BaseType.string(), nullable=False)


def _str() -> ColumnDefinition:
    return ColumnDefinition(data_types=BaseType.string())


def _num() -> ColumnDefinition:
    return ColumnDefinition(data_types=BaseType.numeric(length="18,4"))


def _int() -> ColumnDefinition:
    return ColumnDefinition(data_types=BaseType.integer())


def _bool() -> ColumnDefinition:
    return ColumnDefinition(data_types=BaseType.boolean())


def _timestamp() -> ColumnDefinition:
    return ColumnDefinition(data_types=BaseType.timestamp())


def _date() -> ColumnDefinition:
    return ColumnDefinition(data_types=BaseType.date())


@dataclass(frozen=True)
class ChildTableSpec:
    """One nested array on a parent record, flattened into its own table."""

    table_name: str
    source_field: str
    parent_fk_column: str
    id_field: str
    columns: ColumnSchema

    @property
    def primary_key(self) -> list[str]:
        return [self.parent_fk_column, self.id_field]


@dataclass(frozen=True)
class StreamDefinition:
    """Everything needed to fetch, flatten and write one v1 stream."""

    name: ObjectName
    endpoint: str
    parent_table: str
    parent_primary_key: list[str]
    columns: ColumnSchema
    child_tables: list[ChildTableSpec] = field(default_factory=list)


# --- sales (+ children) ------------------------------------------------

_SALE_LINE_ITEMS = ChildTableSpec(
    table_name="sale_line_items",
    source_field="line_items",
    parent_fk_column="sale_id",
    id_field="id",
    columns={
        "sale_id": _fk(),
        "id": _pk(),
        "product_id": _str(),
        "quantity": _num(),
        "price": _num(),
        "price_total": _num(),
        "price_incl": _num(),
        "price_incl_total": _num(),
        "cost": _num(),
        "cost_total": _num(),
        "tax": _num(),
        "tax_total": _num(),
        "tax_id": _str(),
        "discount_total": _num(),
        "unit_discount": _num(),
        "note": _str(),
        "status": _str(),
        "promotions": _str(),  # nested array, not modelled as a child table -> JSON string
        "customizations": _str(),
        "surcharges": _str(),
    },
)

_SALE_PAYMENTS = ChildTableSpec(
    table_name="sale_payments",
    source_field="payments",
    parent_fk_column="sale_id",
    id_field="id",
    columns={
        "sale_id": _fk(),
        "id": _pk(),
        "payment_type_id": _str(),
        "retailer_payment_type_id": _str(),
        "name": _str(),
        "amount": _num(),
        "register_id": _str(),
        "user_id": _str(),
        "payment_date": _timestamp(),
        "status": _str(),
        "external_applications": _str(),
        "external_attributes": _str(),
    },
)

_SALE_TAXES = ChildTableSpec(
    table_name="sale_taxes",
    source_field="taxes",
    parent_fk_column="sale_id",
    # tax lines commonly have no own `id`; the flattener synthesises a row-index id.
    id_field="id",
    columns={
        "sale_id": _fk(),
        "id": _str(),
        "tax_id": _str(),
        "name": _str(),
        "rate": _num(),
        "total_tax": _num(),
    },
)

_SALE_ADJUSTMENTS = ChildTableSpec(
    table_name="sale_adjustments",
    source_field="adjustments",
    parent_fk_column="sale_id",
    # adjustment lines commonly have no own `id`; the flattener synthesises a row-index id.
    id_field="id",
    columns={
        "sale_id": _fk(),
        "id": _str(),
        "type": _str(),
        "name": _str(),
        "amount": _num(),
    },
)

_SALES = StreamDefinition(
    name=ObjectName.sales,
    endpoint="sales",
    parent_table="sales",
    parent_primary_key=["id"],
    columns={
        "id": _pk(),
        "outlet_id": _str(),
        "register_id": _str(),
        "user_id": _str(),
        "customer_id": _str(),
        "invoice_number": _str(),
        "receipt_number": _str(),
        "status": _str(),
        "note": _str(),
        "short_code": _str(),
        "source": _str(),
        "source_id": _str(),
        "market_id": _str(),
        "register_closure_id": _str(),
        "accounts_transaction_id": _str(),
        "sale_date": _timestamp(),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
        "total_price": _num(),
        "total_price_incl": _num(),
        "total_cost": _num(),
        "total_tax": _num(),
        "total_loyalty": _num(),
        "balance": _num(),
        "total_to_pay": _num(),
        "return_for": _str(),
        "customer": _str(),  # nested object, not modelled as a child table -> JSON string
    },
    child_tables=[_SALE_LINE_ITEMS, _SALE_PAYMENTS, _SALE_TAXES, _SALE_ADJUSTMENTS],
)

# --- products (+ product_images) ---------------------------------------

_PRODUCT_IMAGES = ChildTableSpec(
    table_name="product_images",
    source_field="images",
    parent_fk_column="product_id",
    id_field="id",
    columns={
        "product_id": _fk(),
        "id": _pk(),
        "url": _str(),
        "position": _int(),
    },
)

_PRODUCTS = StreamDefinition(
    name=ObjectName.products,
    endpoint="products",
    parent_table="products",
    parent_primary_key=["id"],
    columns={
        "id": _pk(),
        "source_id": _str(),
        "handle": _str(),
        "has_variants": _bool(),
        "variant_parent_id": _str(),
        "name": _str(),
        "description": _str(),
        "sku": _str(),
        "brand_id": _str(),
        "supplier_id": _str(),
        "supplier_code": _str(),
        "type_id": _str(),
        "tax_id": _str(),
        "price": _num(),
        "supply_price": _num(),
        "loyalty_amount": _num(),
        "track_inventory": _bool(),
        "is_active": _bool(),
        "is_composite": _bool(),
        "tags": _str(),
        "variant_options": _str(),  # nested object, not modelled as a child table -> JSON string
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
        "deleted_at": _timestamp(),
    },
    child_tables=[_PRODUCT_IMAGES],
)

# --- inventory -----------------------------------------------------------
# The X-Series inventory endpoint returns per-outlet stock records that don't
# reliably carry a top-level `id`; the primary key is therefore always the
# (product_id, outlet_id) composite (design spec §2.1). `id` is still kept as
# a plain column when the API does return one.

_INVENTORY = StreamDefinition(
    name=ObjectName.inventory,
    endpoint="inventory",
    parent_table="inventory",
    parent_primary_key=["product_id", "outlet_id"],
    columns={
        "id": _str(),
        "product_id": ColumnDefinition(data_types=BaseType.string(), primary_key=True, nullable=False),
        "outlet_id": ColumnDefinition(data_types=BaseType.string(), primary_key=True, nullable=False),
        "current_amount": _num(),
        "reorder_point": _num(),
        "reorder_amount": _num(),
        "restock_level": _num(),
        "average_cost": _num(),
        "updated_at": _timestamp(),
    },
)

# --- customers -----------------------------------------------------------

_CUSTOMERS = StreamDefinition(
    name=ObjectName.customers,
    endpoint="customers",
    parent_table="customers",
    parent_primary_key=["id"],
    columns={
        "id": _pk(),
        "customer_code": _str(),
        "customer_group_id": _str(),
        "first_name": _str(),
        "last_name": _str(),
        "company_name": _str(),
        "email": _str(),
        "phone": _str(),
        "mobile": _str(),
        "physical_address_1": _str(),
        "physical_city": _str(),
        "physical_state": _str(),
        "physical_postcode": _str(),
        "physical_country_id": _str(),
        "note": _str(),
        "gender": _str(),
        "date_of_birth": _date(),
        "year_to_date": _num(),
        "loyalty_balance": _num(),
        "do_not_email": _bool(),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
        "deleted_at": _timestamp(),
    },
)

# --- outlets ---------------------------------------------------------------

_OUTLETS = StreamDefinition(
    name=ObjectName.outlets,
    endpoint="outlets",
    parent_table="outlets",
    parent_primary_key=["id"],
    columns={
        "id": _pk(),
        "name": _str(),
        "physical_address_1": _str(),
        "physical_city": _str(),
        "physical_state": _str(),
        "physical_postcode": _str(),
        "physical_country_id": _str(),
        "timezone": _str(),
        "currency": _str(),
        "default_tax_id": _str(),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
        "deleted_at": _timestamp(),
    },
)

# --- registers ---------------------------------------------------------------

_REGISTERS = StreamDefinition(
    name=ObjectName.registers,
    endpoint="registers",
    parent_table="registers",
    parent_primary_key=["id"],
    columns={
        "id": _pk(),
        "name": _str(),
        "outlet_id": _str(),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
        "deleted_at": _timestamp(),
    },
)

# --- payment_types ---------------------------------------------------------------

_PAYMENT_TYPES = StreamDefinition(
    name=ObjectName.payment_types,
    endpoint="payment_types",
    parent_table="payment_types",
    parent_primary_key=["id"],
    columns={
        "id": _pk(),
        "name": _str(),
        "type": _str(),
        "is_default": _bool(),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
        "deleted_at": _timestamp(),
    },
)

STREAM_REGISTRY: dict[ObjectName, StreamDefinition] = {
    ObjectName.sales: _SALES,
    ObjectName.products: _PRODUCTS,
    ObjectName.inventory: _INVENTORY,
    ObjectName.customers: _CUSTOMERS,
    ObjectName.outlets: _OUTLETS,
    ObjectName.registers: _REGISTERS,
    ObjectName.payment_types: _PAYMENT_TYPES,
}
