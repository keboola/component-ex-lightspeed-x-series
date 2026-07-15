"""Main component class for keboola.ex-lightspeed-x-series.

`run()` is a thin orchestrator; all real work lives in the private methods
below or in `client.lightspeed_client.LightspeedXSeriesClient` /
`flattener.flatten_records` (design spec §6).
"""

import csv
import logging

from keboola.component.base import ComponentBase, sync_action
from keboola.component.exceptions import UserException

from client.lightspeed_client import LightspeedXSeriesClient
from configuration import Configuration, LoadType
from flattener import FlattenResult, flatten_records
from streams import STREAM_REGISTRY, ChildTableSpec, ColumnSchema, StreamDefinition

_STATE_LAST_VERSION = "last_version"


class Component(ComponentBase):
    def __init__(self):
        super().__init__()
        # Config + client are built here (not in run()) so sync actions can use them too.
        self.params = Configuration(**self.configuration.parameters)
        self.client = self._build_client(self.params)

    def run(self) -> None:
        stream = self._resolve_stream()
        after = self._resolve_start_after(stream)
        result, max_version = self._fetch_and_flatten(stream, after)
        self._write_tables(stream, result)
        self._advance_state(max_version)

    @sync_action("testConnection")
    def test_connection(self) -> None:
        self.client.test_connection()

    def _resolve_stream(self) -> StreamDefinition:
        if self.params.object is None:
            raise UserException("The 'object' parameter is required to run the extractor.")
        return STREAM_REGISTRY[self.params.object]

    def _resolve_start_after(self, stream: StreamDefinition) -> int:
        if self.params.load_type == LoadType.full_load:
            logging.info("Full load requested for '%s' — starting from after=0.", stream.name)
            return 0
        last_version = int(self.get_state_file().get(_STATE_LAST_VERSION, 0))
        logging.info("Incremental load for '%s' — resuming after version %s.", stream.name, last_version)
        return last_version

    def _fetch_and_flatten(self, stream: StreamDefinition, after: int) -> tuple[FlattenResult, int]:
        max_version = after
        result = FlattenResult.empty(stream)
        for page in self.client.paginate(stream.endpoint, after=after, page_size=self.params.page_size):
            result.extend(flatten_records(stream, page.records))
            if page.version_max is not None:
                max_version = page.version_max
        logging.info("Fetched %d '%s' record(s), max version %s.", len(result.parent_rows), stream.name, max_version)
        return result, max_version

    def _write_tables(self, stream: StreamDefinition, result: FlattenResult) -> None:
        self._write_table(stream.parent_table, stream.parent_primary_key, stream.columns, result.parent_rows)
        for child in stream.child_tables:
            self._write_child_table(child, result.child_rows[child.table_name])

    def _write_child_table(self, child: ChildTableSpec, rows: list[dict]) -> None:
        self._write_table(child.table_name, child.primary_key, child.columns, rows)

    def _write_table(self, table_name: str, primary_key: list[str], columns: ColumnSchema, rows: list[dict]) -> None:
        table = self.create_out_table_definition(
            f"{table_name}.csv",
            primary_key=primary_key,
            schema=columns,
            incremental=self.params.incremental,
        )
        with open(table.full_path, "w", encoding="utf-8", newline="") as out_file:
            writer = csv.DictWriter(out_file, fieldnames=list(columns.keys()), extrasaction="ignore")
            writer.writerows(rows)
        self.write_manifest(table)
        logging.info("Wrote %d row(s) to table '%s'.", len(rows), table_name)

    def _advance_state(self, max_version: int) -> None:
        # Persisted only after the output tables above were written successfully —
        # a failed run keeps the old cursor and is safely retried (design spec §2.3).
        self.write_state_file({_STATE_LAST_VERSION: max_version})
        logging.info("Advanced incremental state to last_version=%s.", max_version)

    @staticmethod
    def _build_client(params: Configuration) -> LightspeedXSeriesClient:
        return LightspeedXSeriesClient(
            domain_prefix=params.domain_prefix,
            api_version=params.api_version,
            auth_type=params.auth_type,
            personal_token=params.personal_token,
            client_id=params.client_id,
            client_secret=params.client_secret,
            refresh_token=params.refresh_token,
        )


if __name__ == "__main__":
    try:
        comp = Component()
        # this triggers the run method by default and is controlled by the configuration.action parameter
        comp.execute_action()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
