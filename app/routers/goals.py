"""CRUD endpoints for goals lifecycle (multi-tenant).

Path params are goal names (URL-encoded by the client). FastAPI URL-decodes
them automatically. The cap of `settings.max_active_goals` per user is
enforced inside `app.goals` — this router only translates HTTP to helper
calls and pulls `user_id` from the auth dependency.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import analytics, goals as goals_svc
from ..auth import get_current_user_id
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
async def list_goals_endpoint(
    status: str | None = Query(default=None),
    user_id: UUID = Depends(get_current_user_id),
):
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid status; must be one of {sorted(VALID_STATUSES)}",
        )
    return goals_svc.list_goals(user_id, status=status)


@router.post("")
async def create_goal(body: GoalCreate, user_id: UUID = Depends(get_current_user_id)):
    try:
        result = goals_svc.add_user_goal(body.name, user_id)
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
    analytics.capture(user_id, "goal_created")
    return result


@router.patch("/{name}/fulfill")
async def fulfill_goal(name: str, user_id: UUID = Depends(get_current_user_id)):
    try:
        result = goals_svc.mark_fulfilled(name, user_id)
    except GoalNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"no active goal named '{name}'",
        )
    analytics.capture(user_id, "goal_fulfilled")
    return result


@router.patch("/{name}/rename")
async def rename_goal_endpoint(
    name: str,
    body: GoalRename,
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return goals_svc.rename_goal(name, body.new_name, user_id)
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
async def remove_goal(name: str, user_id: UUID = Depends(get_current_user_id)):
    try:
        result = goals_svc.mark_removed(name, user_id)
    except GoalNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"no goal named '{name}'"
        )
    analytics.capture(user_id, "goal_removed")
    return result
