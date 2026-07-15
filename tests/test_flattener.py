"""Unit tests for src/flattener.py — parent+child flattening (design spec §4.4)."""

import json
import unittest

from configuration import ObjectName
from flattener import FlattenResult, flatten_records
from streams import STREAM_REGISTRY, ChildTableSpec, StreamDefinition


def _child_spec(table_name="children", source_field="items", parent_fk_column="parent_id", id_field="id"):
    return ChildTableSpec(
        table_name=table_name,
        source_field=source_field,
        parent_fk_column=parent_fk_column,
        id_field=id_field,
        columns={parent_fk_column: None, "id": None, "name": None},
    )


def _stream(child_tables=None):
    return StreamDefinition(
        name=ObjectName.sales,
        endpoint="parents",
        parent_table="parents",
        parent_primary_key=["id"],
        columns={"id": None, "name": None, "note": None, "meta": None},
        child_tables=child_tables or [],
    )


class TestFlattenRecordsParentOnly(unittest.TestCase):
    def test_parent_row_extracts_declared_columns_only(self):
        stream = _stream()
        records = [{"id": "1", "name": "Alice", "note": "hello", "unmapped_field": "ignored"}]

        result = flatten_records(stream, records)

        self.assertEqual(len(result.parent_rows), 1)
        self.assertEqual(result.parent_rows[0], {"id": "1", "name": "Alice", "note": "hello", "meta": ""})

    def test_missing_scalar_field_becomes_empty_string(self):
        stream = _stream()
        records = [{"id": "1"}]

        result = flatten_records(stream, records)

        self.assertEqual(result.parent_rows[0]["name"], "")
        self.assertEqual(result.parent_rows[0]["note"], "")

    def test_nested_dict_field_is_json_encoded(self):
        stream = _stream()
        records = [{"id": "1", "meta": {"a": 1, "b": [1, 2]}}]

        result = flatten_records(stream, records)

        self.assertEqual(json.loads(result.parent_rows[0]["meta"]), {"a": 1, "b": [1, 2]})

    def test_nested_list_field_is_json_encoded(self):
        stream = _stream()
        records = [{"id": "1", "meta": ["x", "y"]}]

        result = flatten_records(stream, records)

        self.assertEqual(json.loads(result.parent_rows[0]["meta"]), ["x", "y"])

    def test_multiple_records_produce_multiple_parent_rows(self):
        stream = _stream()
        records = [{"id": "1"}, {"id": "2"}, {"id": "3"}]

        result = flatten_records(stream, records)

        self.assertEqual([row["id"] for row in result.parent_rows], ["1", "2", "3"])

    def test_no_child_tables_leaves_child_rows_empty(self):
        stream = _stream()
        result = flatten_records(stream, [{"id": "1"}])
        self.assertEqual(result.child_rows, {})


class TestFlattenChildTables(unittest.TestCase):
    def test_child_row_carries_parent_fk(self):
        child = _child_spec()
        stream = _stream(child_tables=[child])
        records = [{"id": "p1", "items": [{"id": "c1", "name": "line 1"}]}]

        result = flatten_records(stream, records)

        self.assertEqual(len(result.child_rows["children"]), 1)
        self.assertEqual(result.child_rows["children"][0]["parent_id"], "p1")
        self.assertEqual(result.child_rows["children"][0]["id"], "c1")

    def test_multiple_children_per_parent(self):
        child = _child_spec()
        stream = _stream(child_tables=[child])
        records = [
            {"id": "p1", "items": [{"id": "c1"}, {"id": "c2"}]},
        ]

        result = flatten_records(stream, records)

        self.assertEqual(len(result.child_rows["children"]), 2)
        self.assertEqual([row["id"] for row in result.child_rows["children"]], ["c1", "c2"])

    def test_missing_source_field_yields_no_child_rows(self):
        child = _child_spec()
        stream = _stream(child_tables=[child])
        records = [{"id": "p1"}]  # no "items" key at all

        result = flatten_records(stream, records)

        self.assertEqual(result.child_rows["children"], [])

    def test_null_source_field_yields_no_child_rows(self):
        child = _child_spec()
        stream = _stream(child_tables=[child])
        records = [{"id": "p1", "items": None}]

        result = flatten_records(stream, records)

        self.assertEqual(result.child_rows["children"], [])

    def test_non_dict_items_in_array_are_skipped(self):
        child = _child_spec()
        stream = _stream(child_tables=[child])
        records = [{"id": "p1", "items": ["not-a-dict", 42, {"id": "c1"}]}]

        result = flatten_records(stream, records)

        self.assertEqual(len(result.child_rows["children"]), 1)
        self.assertEqual(result.child_rows["children"][0]["id"], "c1")

    def test_composite_key_synthesized_when_id_missing(self):
        """Tax/adjustment lines commonly have no own `id` (design spec §2.1 / §9 risk #6)."""
        child = _child_spec()
        stream = _stream(child_tables=[child])
        records = [{"id": "p1", "items": [{"name": "no id here"}, {"name": "also none"}]}]

        result = flatten_records(stream, records)

        self.assertEqual(result.child_rows["children"][0]["id"], "0")
        self.assertEqual(result.child_rows["children"][1]["id"], "1")

    def test_composite_key_synthesized_when_id_is_empty_string(self):
        child = _child_spec()
        stream = _stream(child_tables=[child])
        records = [{"id": "p1", "items": [{"id": ""}]}]

        result = flatten_records(stream, records)

        self.assertEqual(result.child_rows["children"][0]["id"], "0")

    def test_multiple_child_table_specs_are_independent(self):
        child_a = _child_spec(table_name="a_children", source_field="a_items")
        child_b = _child_spec(table_name="b_children", source_field="b_items")
        stream = _stream(child_tables=[child_a, child_b])
        records = [{"id": "p1", "a_items": [{"id": "a1"}], "b_items": [{"id": "b1"}, {"id": "b2"}]}]

        result = flatten_records(stream, records)

        self.assertEqual(len(result.child_rows["a_children"]), 1)
        self.assertEqual(len(result.child_rows["b_children"]), 2)

    def test_parent_id_missing_falls_back_to_empty_string(self):
        child = _child_spec()
        stream = _stream(child_tables=[child])
        records = [{"items": [{"id": "c1"}]}]  # no "id" on the parent record

        result = flatten_records(stream, records)

        self.assertEqual(result.parent_rows[0]["id"], "")
        self.assertEqual(result.child_rows["children"][0]["parent_id"], "")


class TestFlattenResult(unittest.TestCase):
    def test_empty_initializes_child_table_keys(self):
        child_a = _child_spec(table_name="a_children")
        child_b = _child_spec(table_name="b_children")
        stream = _stream(child_tables=[child_a, child_b])

        result = FlattenResult.empty(stream)

        self.assertEqual(result.parent_rows, [])
        self.assertEqual(result.child_rows, {"a_children": [], "b_children": []})

    def test_extend_accumulates_across_pages(self):
        stream = _stream(child_tables=[_child_spec()])
        result = FlattenResult.empty(stream)

        result.extend(flatten_records(stream, [{"id": "p1", "items": [{"id": "c1"}]}]))
        result.extend(flatten_records(stream, [{"id": "p2", "items": [{"id": "c2"}]}]))

        self.assertEqual([row["id"] for row in result.parent_rows], ["p1", "p2"])
        self.assertEqual([row["id"] for row in result.child_rows["children"]], ["c1", "c2"])


class TestFlattenRealSalesStreamDefinition(unittest.TestCase):
    """Exercise flattening against the real `sales` registry entry (design spec §4.1)."""

    def test_sale_line_items_use_own_id(self):
        stream = STREAM_REGISTRY[ObjectName.sales]
        records = [
            {
                "id": "sale-1",
                "customer": {"id": "cust-1", "email": "test@example.com"},
                "line_items": [{"id": "li-1", "product_id": "prod-1", "quantity": 2}],
                "payments": [],
                "taxes": [{"tax_id": "tax-1", "rate": "0.2", "total_tax": "1.0"}],
                "adjustments": [],
            }
        ]

        result = flatten_records(stream, records)

        self.assertEqual(result.parent_rows[0]["id"], "sale-1")
        self.assertEqual(json.loads(result.parent_rows[0]["customer"]), {"id": "cust-1", "email": "test@example.com"})
        self.assertEqual(result.child_rows["sale_line_items"][0]["id"], "li-1")
        self.assertEqual(result.child_rows["sale_line_items"][0]["sale_id"], "sale-1")

    def test_sale_taxes_synthesize_composite_id_when_missing(self):
        stream = STREAM_REGISTRY[ObjectName.sales]
        records = [
            {
                "id": "sale-1",
                "line_items": [],
                "payments": [],
                "taxes": [{"tax_id": "tax-1", "rate": "0.2"}, {"tax_id": "tax-2", "rate": "0.1"}],
                "adjustments": [],
            }
        ]

        result = flatten_records(stream, records)

        self.assertEqual(result.child_rows["sale_taxes"][0]["id"], "0")
        self.assertEqual(result.child_rows["sale_taxes"][1]["id"], "1")
        self.assertTrue(all(row["sale_id"] == "sale-1" for row in result.child_rows["sale_taxes"]))

    def test_product_images_child_table(self):
        stream = STREAM_REGISTRY[ObjectName.products]
        records = [
            {
                "id": "prod-1",
                "images": [{"id": "img-1", "url": "https://example.com/1.png", "position": 1}],
            }
        ]

        result = flatten_records(stream, records)

        self.assertEqual(result.child_rows["product_images"][0]["product_id"], "prod-1")
        self.assertEqual(result.child_rows["product_images"][0]["url"], "https://example.com/1.png")


if __name__ == "__main__":
    unittest.main()
