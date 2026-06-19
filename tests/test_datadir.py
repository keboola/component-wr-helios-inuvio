import csv
import os
from pathlib import Path
from unittest import mock

from component import Component


def _datadir(name):
    path = Path(__file__).parent / "data" / name
    os.environ["KBC_DATADIR"] = str(path)
    out = path / "out" / "tables"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*"):
        f.unlink()
    return path


@mock.patch("component.HeliosInuvioClient")
def test_create_load(mock_client_cls):
    _datadir("test_create_load")
    client = mock_client_cls.return_value
    client.write_batch.return_value = [None, None]  # one outcome per row; None == success
    Component().run()
    client.authenticate.assert_called_once()
    browse, mode, *_ = client.write_batch.call_args.args
    assert (browse, mode) == ("organizations", "create")


@mock.patch("component.HeliosInuvioClient")
def test_continue_on_error_writes_failed_records(mock_client_cls):
    path = _datadir("test_continue_on_error")
    client = mock_client_cls.return_value
    client.write_batch.return_value = [None, {"row": {"Name": "Globex", "City": "Brno"}, "error": "boom"}]
    Component().run()
    failed = path / "out" / "tables" / "failed_records.csv"
    assert failed.exists()
    with open(failed) as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["error"] == "boom"
