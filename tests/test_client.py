from unittest import mock

import pytest

from client import HeliosInuvioClient, HeliosInuvioClientError


def _client():
    return HeliosInuvioClient(
        server="http://localhost:8080",
        username="test-api",
        password="secret",
        database="HEE_Helios_001",
    )


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_authenticate_sets_bearer_token(mock_request):
    mock_request.return_value = mock.Mock(status_code=200, json=lambda: {"access_token": "tok-123"}, ok=True)
    client = _client()
    client.authenticate()
    assert client._token == "tok-123"
    assert client._session.headers["Authorization"] == "Bearer tok-123"


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_authenticate_failure_raises_client_error(mock_request):
    mock_request.return_value = mock.Mock(status_code=401, ok=False, text="bad creds", json=lambda: {})
    client = _client()
    with pytest.raises(HeliosInuvioClientError):
        client.authenticate()


def test_session_retries_only_idempotent_methods():
    # Writes (POST/PUT) must NOT be auto-retried — no idempotency key, so a retried write duplicates.
    client = _client()
    retry = client._session.get_adapter("http://localhost").max_retries
    assert set(retry.allowed_methods) == {"GET"}


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_non_json_2xx_response_raises_client_error(mock_request):
    # A 2xx with a non-JSON body must become a HeliosInuvioClientError (-> UserException, exit 1),
    # not an uncaught JSONDecodeError (exit 2).
    def boom():
        raise ValueError("no json")

    mock_request.return_value = mock.Mock(status_code=200, ok=True, json=boom)
    client = _client()
    client._token = "tok"
    with pytest.raises(HeliosInuvioClientError):
        client.list_browses()


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_http_error_message_is_sanitized_and_body_kept_as_detail(mock_request):
    # The user-facing message carries no raw body / internal path; the body is preserved on .detail.
    mock_request.return_value = mock.Mock(
        status_code=422, ok=False, text="TabCisOrg.Nazev is required", json=lambda: {}
    )
    client = _client()
    client._token = "tok"
    with pytest.raises(HeliosInuvioClientError) as exc_info:
        client.write_record("1", "create", None, {"TabCisOrg.Nazev": ""})
    assert "TabCisOrg.Nazev is required" not in str(exc_info.value)
    assert "/api/v1/" not in str(exc_info.value)
    assert exc_info.value.detail == "TabCisOrg.Nazev is required"


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_list_browses_returns_writable_browses(mock_request):
    # GET /api/v1/browses/list/{dbName}; filter to browses the user can create or update.
    mock_request.return_value = mock.Mock(
        status_code=200,
        ok=True,
        json=lambda: {
            "count": 2,
            "browses": [
                {
                    "browseIdent": 1,
                    "browseCaption": "Organizace",
                    "browseCreateImplemented": True,
                    "browseUserRightCreate": True,
                    "browseUpdateImplemented": True,
                    "browseUserRightUpdate": True,
                },
                {
                    "browseIdent": 9,
                    "browseCaption": "ReadOnly",
                    "browseCreateImplemented": False,
                    "browseUserRightCreate": False,
                    "browseUpdateImplemented": False,
                    "browseUserRightUpdate": False,
                },
            ],
        },
    )
    client = _client()
    client._token = "tok"
    browses = client.list_browses()
    assert browses == [{"name": "1", "label": "Organizace"}]
    # dbName is threaded into the URL
    assert mock_request.call_args.args[1].endswith("/api/v1/browses/list/HEE_Helios_001")


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_get_writable_fields(mock_request):
    # GET /api/v1/browses/meta/{dbName}/{browseIdent}; fields under table.tableFields[].
    mock_request.return_value = mock.Mock(
        status_code=200,
        ok=True,
        json=lambda: {
            "table": {
                "tableFields": [
                    {"fieldSysName": "TabCisOrg.Nazev", "fieldShortDisplayName": "Název", "fieldType": "string"},
                    {
                        "fieldSysName": "TabCisOrg.Internal",
                        "fieldShortDisplayName": "X",
                        "fieldType": "string",
                        "fieldDisabledForCreate": True,
                    },
                ]
            }
        },
    )
    client = _client()
    client._token = "tok"
    fields = client.get_writable_fields("1")
    assert [f["field_name"] for f in fields] == ["TabCisOrg.Nazev"]  # disabled-for-create dropped
    assert fields[0]["label"] == "Název"
    assert mock_request.call_args.args[1].endswith("/api/v1/browses/meta/HEE_Helios_001/1")


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_write_record_create_posts(mock_request):
    mock_request.return_value = mock.Mock(status_code=201, ok=True, json=lambda: {"id": 1})
    client = _client()
    client._token = "tok"
    result = client.write_record("1", "create", None, {"TabCisOrg.Nazev": "Acme"})
    args, kwargs = mock_request.call_args
    assert args[0] == "POST"
    assert args[1].endswith("/api/v1/browses/data/HEE_Helios_001/1")
    assert kwargs["json"] == {"row": {"TabCisOrg.Nazev": "Acme"}}  # payload wrapped under "row"
    assert result == {"id": 1}


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_write_record_update_puts_with_key_in_path(mock_request):
    mock_request.return_value = mock.Mock(status_code=200, ok=True, json=lambda: {})
    client = _client()
    client._token = "tok"
    # key_field "id" holds the Helios recordId; it becomes the {recordId} path segment,
    # and is popped out of the row body.
    client.write_record("1", "update", "id", {"id": "42", "TabCisOrg.Nazev": "Acme"})
    args, kwargs = mock_request.call_args
    assert args[0] == "PUT"
    assert args[1].endswith("/api/v1/browses/data/HEE_Helios_001/1/42")
    assert kwargs["json"] == {"row": {"TabCisOrg.Nazev": "Acme"}}  # key popped from body


def test_write_batch_yields_outcome_per_row_when_continue():
    # write_batch is a generator: None per success, a {"row","error"} dict per failure.
    client = _client()
    client._token = "tok"
    calls = []

    def fake_write(browse, mode, key_field, payload):
        calls.append(payload)
        if payload["Name"] == "BAD":
            raise HeliosInuvioClientError("rejected")
        return {"ok": True}

    client.write_record = fake_write  # type: ignore[assignment]
    rows = [{"Name": "GOOD"}, {"Name": "BAD"}, {"Name": "GOOD2"}]
    outcomes = list(client.write_batch("organizations", "create", None, rows, continue_on_error=True))
    assert outcomes == [None, {"row": {"Name": "BAD"}, "error": "rejected"}, None]
    assert sum(1 for o in outcomes if o is None) == 2


def test_write_batch_failure_uses_detail_for_error_column():
    # The user-safe message stays generic; the raw API body (detail) is what lands in the CSV.
    client = _client()
    client._token = "tok"

    def fake_write(browse, mode, key_field, payload):
        raise HeliosInuvioClientError("Helios iNuvio rejected the record (HTTP 422).", detail="field X is required")

    client.write_record = fake_write  # type: ignore[assignment]
    outcomes = list(client.write_batch("organizations", "create", None, [{"Name": "X"}], continue_on_error=True))
    assert outcomes[0]["error"] == "field X is required"


def test_write_batch_raises_when_not_continue():
    client = _client()
    client._token = "tok"

    def fake_write(browse, mode, key_field, payload):
        raise HeliosInuvioClientError("rejected")

    client.write_record = fake_write  # type: ignore[assignment]
    with pytest.raises(HeliosInuvioClientError):
        list(client.write_batch("organizations", "create", None, [{"Name": "X"}], continue_on_error=False))


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_write_batch_continues_on_data_rejection(mock_request):
    # A 422 is a per-row data rejection: continue_on_error captures it and carries on.
    mock_request.return_value = mock.Mock(status_code=422, ok=False, text="field X is required", json=lambda: {})
    client = _client()
    client._token = "tok"
    outcomes = list(client.write_batch("1", "create", None, [{"Name": "X"}], continue_on_error=True))
    assert outcomes == [{"row": {"Name": "X"}, "error": "field X is required"}]


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_write_batch_aborts_on_fatal_status_despite_continue(mock_request):
    # A 5xx is fatal to the whole batch — continue_on_error must NOT swallow it as a per-row
    # failure, otherwise an outage looks like a CSV full of rejected records and the job exits 0.
    mock_request.return_value = mock.Mock(status_code=503, ok=False, text="unavailable", json=lambda: {})
    client = _client()
    client._token = "tok"
    with pytest.raises(HeliosInuvioClientError) as exc_info:
        list(client.write_batch("1", "create", None, [{"Name": "X"}], continue_on_error=True))
    assert exc_info.value.fatal is True


@mock.patch("client.helios_inuvio_client.requests.Session.request")
def test_transport_error_is_fatal_despite_continue(mock_request):
    # Connection loss is never a per-row problem; it aborts the batch even under continue_on_error.
    import requests as _requests

    mock_request.side_effect = _requests.ConnectionError("connection dropped")
    client = _client()
    client._token = "tok"
    with pytest.raises(HeliosInuvioClientError) as exc_info:
        list(client.write_batch("1", "create", None, [{"Name": "X"}], continue_on_error=True))
    assert exc_info.value.fatal is True
