from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.broker import ledger, service

router = APIRouter(prefix="/api/broker", tags=["broker"])

UserId = Field(min_length=1, max_length=100)


class ExecuteRequest(BaseModel):
    user_id: str = UserId
    alert_id: str = Field(min_length=1, max_length=64)
    holdings: list[dict] | None = None
    scenario: str | None = None


class PolicyRequest(BaseModel):
    user_id: str = UserId
    enabled: bool
    # Auto-execute when the downside is at least this share of the position...
    min_downside: float = Field(ge=0.01, le=0.5)
    # ...and, for a hedge, the put costs no more than this share of it.
    max_put_cost: float = Field(ge=0.001, le=0.2)


class AutoRequest(BaseModel):
    user_id: str = UserId
    holdings: list[dict] | None = None


@router.get("/account")
def account(user_id: str) -> dict:
    """The paper account's policy, executions and totals."""
    return service.account(user_id)


@router.post("/execute")
def execute(request: ExecuteRequest) -> dict:
    """Execute an active alert's recommended action in the paper account."""
    try:
        return service.execute(request.user_id, request.alert_id, request.holdings, request.scenario)
    except service.NotExecutable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/policy")
def set_policy(request: PolicyRequest) -> dict:
    return ledger.set_policy(request.user_id, request.model_dump(exclude={"user_id"}))


@router.post("/auto")
def auto(request: AutoRequest) -> dict:
    """Run the user's policy against the alerts active now. Does nothing unless the policy is switched on."""
    return service.run_policy(request.user_id, request.holdings)
