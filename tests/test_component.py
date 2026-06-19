import os
from unittest import mock

import pytest
from keboola.component.exceptions import UserException

from component import Component
from configuration import FieldMapping

CONN = {"server": "http://localhost:8080", "username": "u", "#password": "p", "database": "db"}


def test_component_imports():
    assert Component is not None


def test_active_mapping_filters_unmapped_blank_and_absent():
    headers = ["a", "b", "c"]
    mapping = [
        FieldMapping(source_column="a", destination_field="Alpha"),
        FieldMapping(source_column="b", destination_field=""),  # blank destination -> dropped
        FieldMapping(source_column="missing", destination_field="X"),  # not in headers -> dropped
        FieldMapping(source_column="c", destination_field="Gamma"),
    ]
    assert Component._active_mapping(headers, mapping) == [("a", "Alpha"), ("c", "Gamma")]


def _csv(tmp_path, text):
    p = tmp_path / "input.csv"
    p.write_text(text)
    return str(p)


def test_payloads_filters_and_renames(tmp_path):
    path = _csv(tmp_path, "a,b,c\n1,2,3\n4,5,6\n")
    active = [("a", "Alpha"), ("c", "Gamma")]
    assert list(Component._payloads(path, active)) == [
        {"Alpha": "1", "Gamma": "3"},
        {"Alpha": "4", "Gamma": "6"},
    ]


def test_payloads_handles_ragged_rows(tmp_path):
    # A short data row (fewer fields than the header) must not crash; DictReader yields no value
    # for the missing column, which becomes an empty string rather than an IndexError / exit 2.
    path = _csv(tmp_path, "a,b,c\n1,2\n")  # row is missing the "c" column
    active = [("a", "Alpha"), ("c", "Gamma")]
    assert list(Component._payloads(path, active)) == [{"Alpha": "1", "Gamma": ""}]


def _write_config(tmp_path, *, continue_on_error=False, write_mode="create", key_field=""):
    import json

    data = tmp_path / "data"
    (data / "in" / "tables").mkdir(parents=True)
    (data / "out" / "tables").mkdir(parents=True)
    (data / "in" / "tables" / "input.csv").write_text("a,b\n1,2\n3,4\n")
    (data / "in" / "tables" / "input.csv.manifest").write_text(
        json.dumps({"id": "in.c-main.input", "name": "input", "columns": ["a", "b"]})
    )
    config = {
        "parameters": {
            "connection": CONN,
            "browse": "organizations",
            "write_mode": write_mode,
            "key_field": key_field,
            "field_mapping": [
                {"source_column": "a", "destination_field": "Alpha"},
                {"source_column": "b", "destination_field": "Beta"},
            ],
            "continue_on_error": continue_on_error,
        },
        "storage": {"input": {"tables": [{"source": "in.c-main.input", "destination": "input.csv"}]}},
    }
    (data / "config.json").write_text(json.dumps(config))
    return data


@mock.patch("component.HeliosInuvioClient")
def test_run_writes_all_rows(mock_client_cls, tmp_path):
    data = _write_config(tmp_path)
    client = mock_client_cls.return_value
    client.write_batch.return_value = [None, None]  # one outcome per row; None == success
    os.environ["KBC_DATADIR"] = str(data)

    Component().run()

    client.authenticate.assert_called_once()
    browse, mode, key_field, payloads, *_ = client.write_batch.call_args.args
    assert browse == "organizations"
    assert mode == "create"
    assert list(payloads) == [{"Alpha": "1", "Beta": "2"}, {"Alpha": "3", "Beta": "4"}]


@mock.patch("component.HeliosInuvioClient")
def test_run_writes_failed_records_csv(mock_client_cls, tmp_path):
    import csv as _csv
    import json as _json

    data = _write_config(tmp_path, continue_on_error=True)
    client = mock_client_cls.return_value
    # One success, one failure — write_batch yields an outcome per row as it is processed.
    client.write_batch.return_value = [None, {"row": {"Alpha": "3", "Beta": "4"}, "error": "boom"}]
    os.environ["KBC_DATADIR"] = str(data)

    Component().run()

    failed = data / "out" / "tables" / "failed_records.csv"
    assert failed.exists()
    with open(failed) as f:
        rows = list(_csv.DictReader(f))
    # Fixed, browse-agnostic schema; the rejected record is captured as a JSON payload + error.
    assert set(rows[0].keys()) == {"config_row_id", "primary_key", "payload", "error"}
    assert rows[0]["error"] == "boom"
    assert rows[0]["primary_key"] == ""  # create mode has no key field
    assert _json.loads(rows[0]["payload"]) == {"Alpha": "3", "Beta": "4"}
    # The manifest is written (destination is resolved by the component's defaultBucket).
    assert (data / "out" / "tables" / "failed_records.csv.manifest").exists()


@mock.patch("component.HeliosInuvioClient")
def test_run_without_failures_emits_no_failed_records_table(mock_client_cls, tmp_path):
    data = _write_config(tmp_path, continue_on_error=True)
    client = mock_client_cls.return_value
    client.write_batch.return_value = [None, None]
    os.environ["KBC_DATADIR"] = str(data)

    Component().run()

    assert not (data / "out" / "tables" / "failed_records.csv").exists()
    assert not (data / "out" / "tables" / "failed_records.csv.manifest").exists()


@mock.patch("component.HeliosInuvioClient")
def test_run_update_without_key_field_raises(mock_client_cls, tmp_path):
    data = _write_config(tmp_path, write_mode="update", key_field="")
    os.environ["KBC_DATADIR"] = str(data)
    with pytest.raises(UserException):
        Component().run()


@mock.patch("component.HeliosInuvioClient")
def test_run_wraps_client_error_as_userexception(mock_client_cls, tmp_path):
    from client import HeliosInuvioClientError

    data = _write_config(tmp_path)
    os.environ["KBC_DATADIR"] = str(data)
    mock_client_cls.return_value.authenticate.side_effect = HeliosInuvioClientError("auth boom")
    with pytest.raises(UserException):
        Component().run()


@mock.patch("component.HeliosInuvioClient")
def test_run_empty_field_mapping_raises(mock_client_cls, tmp_path):
    import json

    data = _write_config(tmp_path)
    # overwrite config.json with an empty field_mapping
    cfg_path = data / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["parameters"]["field_mapping"] = []
    cfg_path.write_text(json.dumps(cfg))
    os.environ["KBC_DATADIR"] = str(data)
    with pytest.raises(UserException):
        Component().run()


@mock.patch("component.HeliosInuvioClient")
def test_test_connection_success(mock_client_cls, tmp_path):
    import json

    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text(
        json.dumps(
            {
                "action": "testConnection",
                "parameters": {"connection": CONN},
            }
        )
    )
    os.environ["KBC_DATADIR"] = str(data)
    result = Component().test_connection()
    assert result["status"] == "success"
    mock_client_cls.return_value.authenticate.assert_called_once()


@mock.patch("component.HeliosInuvioClient")
def test_test_connection_failure_raises_userexception(mock_client_cls, tmp_path):
    import json

    from client import HeliosInuvioClientError

    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text(
        json.dumps(
            {
                # action == "run" so the @sync_action wrapper re-raises the UserException
                # instead of converting it to exit(1) (the wrapper only swallows exceptions
                # when running under a real sync-action dispatch). This lets us assert the
                # inner failure contract directly.
                "action": "run",
                "parameters": {"connection": {**CONN, "#password": "bad"}},
            }
        )
    )
    os.environ["KBC_DATADIR"] = str(data)
    mock_client_cls.return_value.authenticate.side_effect = HeliosInuvioClientError("401")
    with pytest.raises(UserException):
        Component().test_connection()


@mock.patch("component.HeliosInuvioClient")
def test_list_browses_returns_value_only_select_elements(mock_client_cls, tmp_path):
    import json

    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text(
        json.dumps(
            {
                "action": "list_browses",
                "parameters": {"connection": CONN},
            }
        )
    )
    os.environ["KBC_DATADIR"] = str(data)
    client = mock_client_cls.return_value
    client.list_browses.return_value = [
        {"name": "1", "label": "Organizace"},
        {"name": "2", "label": None},
    ]
    result = Component().list_browses()
    # value == label, formatted "Browse Name (id)"; bare id when the browse has no caption
    assert [(e.value, e.label) for e in result] == [("Organizace (1)", "Organizace (1)"), ("2", "2")]


def test_parse_browse_id_extracts_trailing_id_or_passes_through():
    assert Component._parse_browse_id("Organizace (1)") == "1"
    assert Component._parse_browse_id("Polozky (v priprave) (962)") == "962"
    assert Component._parse_browse_id("1") == "1"
    assert Component._parse_browse_id("  86  ") == "86"


@mock.patch("component.HeliosInuvioClient")
def test_load_field_mapping_fuzzy_matches_and_preserves(mock_client_cls, tmp_path):
    import json

    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text(
        json.dumps(
            {
                "action": "load_field_mapping",
                "parameters": {
                    "connection": CONN,
                    "browse": "organizations",
                    "field_mapping": [{"source_column": "city", "destination_field": "PreSet"}],
                },
                "storage": {
                    "input": {
                        "tables": [
                            {"source": "in.c-main.input", "destination": "input.csv", "columns": ["name", "city"]}
                        ]
                    }
                },
            }
        )
    )
    os.environ["KBC_DATADIR"] = str(data)
    client = mock_client_cls.return_value
    client.get_writable_fields.return_value = [
        {"field_name": "Name", "label": "Name"},
        {"field_name": "City", "label": "City"},
    ]
    result = Component().load_field_mapping()
    mapping = {fm["source_column"]: fm["destination_field"] for fm in result["data"]["field_mapping"]}
    assert mapping["name"] == "Name"  # fuzzy: case-insensitive match
    assert mapping["city"] == "PreSet"  # existing assignment preserved
    assert result["data"]["_metadata_"]["api_fields"][0]["field_name"] == "Name"


@mock.patch("component.HeliosInuvioClient")
def test_load_field_mapping_requires_browse(mock_client_cls, tmp_path):
    import json

    data = tmp_path / "data"
    data.mkdir()
    # action="run" so the @sync_action wrapper re-raises UserException instead of exit(1);
    # see the library note discovered in Task 8.
    (data / "config.json").write_text(
        json.dumps(
            {
                "action": "run",
                "parameters": {"connection": CONN, "browse": ""},
            }
        )
    )
    os.environ["KBC_DATADIR"] = str(data)
    with pytest.raises(UserException):
        Component().load_field_mapping()
