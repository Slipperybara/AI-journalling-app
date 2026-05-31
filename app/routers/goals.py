"""CRUD endpoints for goals lifecycle.

Path params are goal names (URL-encoded by the client). FastAPI URL-decodes
them automatically. The cap of 3 active goals is enforced inside
`app.goals` — this router only translates between HTTP and helper calls.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import goals as goals_svc
from ..goals import (
    GoalCapReachedError,
    GoalExistsError,
    GoalNotFoundError,
    VALID_STATUSES,
)


router = APIRouter(prefix="/api/goals", tags=["goals"])


class GoalCreate(BaseModel):
    name: str


class GoalRename(BaseModel):
    new_name: str


@router.get("")
async def list_goals_endpoint(status: str | None = Query(default=None)):
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid status; must be one of {sorted(VALID_STATUSES)}",
        )
    return goals_svc.list_goals(status=status)


@router.post("")
async def create_goal(body: GoalCreate):
    try:
        return goals_svc.add_user_goal(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except GoalExistsError:
        raise HTTPException(
            status_code=409, detail=f"goal '{body.name}' already exists"
        )
    except GoalCapReachedError:
        raise HTTPException(
            status_code=409,
            detail="3 active goals already; fulfill or remove one first",
        )


@router.patch("/{name}/fulfill")
async def fulfill_goal(name: str):
    try:
        return goals_svc.mark_fulfilled(name)
    except GoalNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"no active goal named '{name}'",
        )


@router.patch("/{name}/rename")
async def rename_goal_endpoint(name: str, body: GoalRename):
    try:
        return goals_svc.rename_goal(name, body.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except GoalNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"no active/fulfilled goal named '{name}'"
        )
    except GoalExistsError:
        raise HTTPException(
            status_code=409,
            detail=f"a goal named '{body.new_name}' already exists",
        )


@router.delete("/{name}")
async def remove_goal(name: str):
    try:
        return goals_svc.mark_removed(name)
    except GoalNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"no goal named '{name}'"
        )
