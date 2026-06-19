from enum import StrEnum

from keboola.component.exceptions import UserException
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class WriteMode(StrEnum):
    CREATE = "create"
    UPDATE = "update"


class FieldMapping(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source_column: str = ""
    destination_field: str = ""


class Connection(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    server: str
    username: str
    password: str = Field(alias="#password")
    database: str


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    def __init__(self, **data):
        try:
            super().__init__(**data)
        except ValidationError as e:
            messages = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
            raise UserException(f"Validation Error: {', '.join(messages)}") from e


class Configuration(_Base):
    connection: Connection
    browse: str = ""
    write_mode: WriteMode = WriteMode.CREATE
    key_field: str = ""
    field_mapping: list[FieldMapping] = Field(default_factory=list)
    continue_on_error: bool = False
    debug: bool = False
