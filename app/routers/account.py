"""Account deletion endpoint (multi-tenant).

`DELETE /api/account` erases the authenticated user's app data (Postgres rows +
Neo4j subgraph). The client follows up with the `delete_account` Supabase RPC to
remove the auth identity, then signs out. `user_id` comes from the auth
dependency, so a user can only ever delete themselves.
"""
from uuid import UUID

from fastapi import APIRouter, Depends

from ..account import delete_account
from ..auth import get_current_user_id


router = APIRouter(prefix="/api/account", tags=["account"])


@router.delete("")
async def delete_account_endpoint(user_id: UUID = Depends(get_current_user_id)):
    return delete_account(user_id)
