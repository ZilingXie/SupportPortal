"""Staging/preproduction-only rerun request contract."""

from pydantic import BaseModel, ConfigDict, Field

from backend.services.automation_contracts import AutomationEnvironment


RERUN_CAPABILITIES: dict[AutomationEnvironment, tuple[bool, bool]] = {
    AutomationEnvironment.STAGING: (True, True),
    AutomationEnvironment.PREPRODUCTION: (True, False),
    AutomationEnvironment.PRODUCTION: (False, False),
}


def rerun_capabilities(environment: AutomationEnvironment) -> tuple[bool, bool]:
    """Return (rerun, reset) capabilities for the runtime that owns them."""
    return RERUN_CAPABILITIES[AutomationEnvironment(environment)]


class RerunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=160)
    case_id: str = Field(min_length=1, max_length=160)
    rerun_of_execution_id: str = Field(min_length=1, max_length=160)
