"""Staging/preproduction-only rerun request contract."""

from pydantic import BaseModel, ConfigDict, Field


class RerunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=160)
    case_id: str = Field(min_length=1, max_length=160)
    rerun_of_execution_id: str = Field(min_length=1, max_length=160)
