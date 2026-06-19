import pytest
from keboola.component.exceptions import UserException

from configuration import Configuration, WriteMode

CONN = {
    "server": "http://localhost:8080",
    "username": "test-api",
    "#password": "secret",
    "database": "HEE_Helios_001",
}


def _config(**params) -> Configuration:
    # Mirror production: Component builds Configuration(**self.configuration.parameters) from the
    # raw parameters dict. Going through **params keeps the tests faithful to that path and lets
    # pydantic coerce the nested connection mapping (and the #password alias) as it does at runtime.
    return Configuration(**params)


def test_configuration_parses_nested_connection_and_password_alias():
    config = _config(connection=CONN)
    assert config.connection.server == "http://localhost:8080"
    assert config.connection.password == "secret"


def test_configuration_missing_connection_raises_userexception():
    with pytest.raises(UserException):
        _config()


def test_configuration_incomplete_connection_raises_userexception():
    with pytest.raises(UserException):
        _config(connection={"server": "http://localhost:8080"})


def test_write_target_defaults_are_safe_for_sync_actions():
    # async-loaded write-target fields must default to empty with no rejecting validators
    config = _config(connection=CONN)
    assert config.browse == ""
    assert config.write_mode == WriteMode.CREATE
    assert config.key_field == ""
    assert config.field_mapping == []
    assert config.continue_on_error is False


def test_write_mode_enum():
    config = _config(connection=CONN, write_mode="update", key_field="id")
    assert config.write_mode == WriteMode.UPDATE
