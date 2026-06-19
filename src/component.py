import csv
import json
import logging
import re
from collections.abc import Iterator
from typing import IO, Any

from keboola.component.base import ComponentBase, sync_action
from keboola.component.dao import TableDefinition
from keboola.component.exceptions import UserException
from keboola.component.sync_actions import SelectElement
from keboola.vcr import DefaultSanitizer

from client import HeliosInuvioClient, HeliosInuvioClientError
from configuration import Configuration, Connection, FieldMapping

# Read by the datadirtest VCR scaffolder when recording cassettes. keboola.vcr ships as a
# transitive dependency of keboola.component, so this import is safe in the --no-dev image.
VCR_SANITIZERS = [
    DefaultSanitizer(additional_sensitive_fields=["windowsAuthentication", "grant_type"]),
]

FAILED_RECORDS_TABLE = "failed_records.csv"

FAILED_RECORDS_SCHEMA = ["config_row_id", "primary_key", "payload", "error"]


class _FailedRecordsWriter:
    """Streams rejected records to failed_records.csv, opening the file lazily on the first failure
    and flushing after every row, so a mid-run crash keeps the failures captured so far. The table
    is append-only (incremental load, no primary key) with a fixed schema, so every config row and
    run accumulates into the same table without column collisions. Emitted only on a real failure."""

    def __init__(self, component: ComponentBase, config_row_id: str, key_field: str):
        self._component = component
        self._config_row_id = config_row_id
        self._key_field = key_field
        self._file: IO[str] | None = None
        self._writer: csv.DictWriter | None = None
        self._out_def: TableDefinition | None = None
        self.count = 0

    def write(self, failure: dict[str, Any]) -> None:
        if self._writer is None:
            # No explicit destination: the table relies on the component's `defaultBucket`
            # (configured in the Developer Portal). incremental=True with no primary key makes
            # it append-only, so failures from every row/run stack up rather than overwrite.
            self._out_def = self._component.create_out_table_definition(
                FAILED_RECORDS_TABLE,
                write_always=True,
                incremental=True,
                primary_key=[],
                schema=FAILED_RECORDS_SCHEMA,
                has_header=True,
            )
            self._file = open(self._out_def.full_path, "w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._file, fieldnames=FAILED_RECORDS_SCHEMA, extrasaction="ignore")
            self._writer.writeheader()
        row = failure["row"]
        self._writer.writerow(
            {
                "config_row_id": self._config_row_id,
                # The Helios record id for update mode (the key field's value); empty for create.
                "primary_key": str(row.get(self._key_field, "")) if self._key_field else "",
                "payload": json.dumps(row, ensure_ascii=False, sort_keys=True),
                "error": failure["error"],
            }
        )
        assert self._file is not None  # opened together with self._writer above
        self._file.flush()
        self.count += 1

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
        if self._out_def is not None:
            self._component.write_manifest(self._out_def)


class Component(ComponentBase):
    def __init__(self) -> None:
        super().__init__()
        self._config = Configuration(**self.configuration.parameters)
        self._client = self._build_client(self._config.connection)

    def run(self) -> None:
        config = self._config

        if not config.browse:
            raise UserException("No browse selected.")
        if config.write_mode.value == "update" and not config.key_field:
            raise UserException("Update mode requires a key field.")
        if not config.field_mapping:
            raise UserException("Field mapping is empty — nothing to write.")

        table = self._resolve_input_table()
        active = self._active_mapping(self._read_headers(table.full_path), config.field_mapping)
        payloads = self._payloads(table.full_path, active)

        # The failed-records writer (and its open file handle) only exists when continue_on_error
        # is on — otherwise write_batch raises on the first rejection and no failures are collected,
        # so there is nothing to write and no reason to hold the writer for the whole run.
        failed = (
            _FailedRecordsWriter(self, self.environment_variables.config_row_id or "", config.key_field)
            if config.continue_on_error
            else None
        )
        ok = 0
        try:
            self._client.authenticate()
            for outcome in self._client.write_batch(
                self._parse_browse_id(config.browse),
                config.write_mode.value,
                config.key_field or None,
                payloads,
                continue_on_error=config.continue_on_error,
            ):
                if outcome is None:
                    ok += 1
                elif failed is not None:
                    failed.write(outcome)
        except HeliosInuvioClientError as exc:
            raise UserException(str(exc)) from exc
        finally:
            if failed is not None:
                failed.close()

        logging.info("Wrote %d record(s) to '%s'.", ok, config.browse)
        if failed is not None and failed.count:
            logging.warning("%d record(s) failed — see %s.", failed.count, FAILED_RECORDS_TABLE)

    @staticmethod
    def _build_client(connection: Connection) -> HeliosInuvioClient:
        return HeliosInuvioClient(
            server=connection.server,
            username=connection.username,
            password=connection.password,
            database=connection.database,
        )

    @sync_action("testConnection")
    def test_connection(self) -> dict[str, Any]:
        try:
            self._client.authenticate()
        except HeliosInuvioClientError as exc:
            raise UserException(f"Connection failed: {exc}") from exc
        return {"status": "success", "message": "Connection successful"}

    @sync_action("list_browses")
    def list_browses(self) -> list[SelectElement]:
        try:
            self._client.authenticate()
            browses = self._client.list_browses()
        except HeliosInuvioClientError as exc:
            raise UserException(str(exc)) from exc
        # "Browse Name (id)" is both the stored value and the display label; run() parses the id back out.
        return [SelectElement(value=self._format_browse(b), label=self._format_browse(b)) for b in browses]

    @sync_action("load_field_mapping")
    def load_field_mapping(self) -> dict[str, Any]:
        config = self._config
        if not config.browse:
            raise UserException("Please select a browse first.")

        input_mappings = self.configuration.tables_input_mapping
        if len(input_mappings) != 1:
            raise UserException("Exactly one input table must be configured.")
        table = input_mappings[0]
        columns = table.columns or self._get_columns_from_manifest(table.source)

        try:
            self._client.authenticate()
            api_fields = self._client.get_writable_fields(self._parse_browse_id(config.browse))
        except HeliosInuvioClientError as exc:
            raise UserException(str(exc)) from exc

        field_names = [f["field_name"] for f in api_fields]
        existing = {fm.source_column: fm.destination_field for fm in config.field_mapping}
        field_mapping = [
            {
                "source_column": col,
                "destination_field": existing.get(col) or self._fuzzy_match(col, field_names),
            }
            for col in columns
        ]
        return {
            "type": "data",
            "data": {
                "browse": config.browse,
                "field_mapping": field_mapping,
                "_metadata_": {"api_fields": api_fields},
            },
        }

    def _get_columns_from_manifest(self, source_table: str) -> list[str]:
        for table in self.get_input_tables_definitions():
            if table.name == source_table or source_table.endswith(table.name):
                return list(table.columns) if table.columns else []
        raise UserException(
            f"Could not determine columns for '{source_table}'. Configure explicit columns in the input mapping."
        )

    @staticmethod
    def _format_browse(browse: dict[str, Any]) -> str:
        name, label = browse["name"], browse.get("label")
        return f"{label} ({name})" if label else name

    @staticmethod
    def _parse_browse_id(value: str) -> str:
        # Accept "Browse Name (id)" from the dropdown or a bare id typed by hand.
        match = re.search(r"\(([^()]+)\)\s*$", value)
        return match.group(1).strip() if match else value.strip()

    @staticmethod
    def _fuzzy_match(column: str, field_names: list[str]) -> str:
        col_norm = column.lower().replace("_", "").replace(" ", "")
        for field in field_names:
            if field.lower().replace("_", "").replace(" ", "") == col_norm:
                return field
        return ""

    def _resolve_input_table(self) -> TableDefinition:
        tables = self.get_input_tables_definitions()
        if len(tables) != 1:
            raise UserException(f"Exactly one input table must be mapped, found {len(tables)}.")
        return tables[0]

    @staticmethod
    def _read_headers(path: str) -> list[str]:
        with open(path, newline="", encoding="utf-8") as f:
            return next(csv.reader(f), [])

    @staticmethod
    def _active_mapping(headers: list[str], mapping: list[FieldMapping]) -> list[tuple[str, str]]:
        # (source_column, destination_field) for each mapped column actually present in the input,
        # in mapping order. Drives both the written payloads and the failed_records header.
        return [
            (m.source_column, m.destination_field)
            for m in mapping
            if m.destination_field and m.source_column in headers
        ]

    @staticmethod
    def _payloads(path: str, active: list[tuple[str, str]]) -> Iterator[dict[str, str]]:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                # DictReader keys each row by column name, so a short/ragged row simply has no
                # value for the missing columns (None -> "") and there is no positional indexing
                # that could raise IndexError.
                yield {dest: (row.get(src) or "") for src, dest in active}


if __name__ == "__main__":
    try:
        comp = Component()
        comp.execute_action()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
