from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import analytics
from ..auth import get_current_user_id
from ..bot import store_assistant_message
from ..db import connect
from ..intro_greeting import generate_intro_greeting


router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationRename(BaseModel):
    title: str


@router.post("")
async def create_conversation(user_id: UUID = Depends(get_current_user_id)):
    started_at = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conversations (user_id, started_at) VALUES (%s, %s) RETURNING id",
            (str(user_id), started_at),
        )
        conv_id = cursor.fetchone()["id"]
    analytics.capture(user_id, "conversation_created", {"conversation_id": conv_id})
    return {"id": conv_id, "started_at": started_at}


@router.post("/welcome")
async def welcome_conversation(user_id: UUID = Depends(get_current_user_id)):
    """Idempotently seed a brand-new user's first conversation with a warm,
    personalized greeting grounded in their onboarding profile. If the user
    already has any conversation, this is a no-op that returns the most recent one
    (so it can't double-greet). Called once by the client after the onboarding
    profile has synced."""
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM conversations WHERE user_id = %s AND archived = FALSE "
            "ORDER BY started_at DESC LIMIT 1",
            (str(user_id),),
        )
        existing = cursor.fetchone()
    if existing is not None:
        return {"id": existing["id"], "created": False}

    greeting = generate_intro_greeting(user_id)
    started_at = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conversations (user_id, started_at) VALUES (%s, %s) RETURNING id",
            (str(user_id), started_at),
        )
        conv_id = cursor.fetchone()["id"]
    store_assistant_message(conv_id, greeting, user_id)
    analytics.capture(user_id, "welcome_greeting_posted", {"conversation_id": conv_id})
    return {"id": conv_id, "created": True}


@router.get("")
async def list_conversations(user_id: UUID = Depends(get_current_user_id)):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.started_at, c.title,
                   COUNT(m.id) AS message_count,
                   MAX(m.created_at) AS last_message_at,
                   (
                       SELECT content FROM messages
                       WHERE conversation_id = c.id AND user_id = %s AND role = 'user'
                       ORDER BY id ASC LIMIT 1
                   ) AS first_user_message
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id AND m.user_id = %s
            WHERE c.user_id = %s AND c.archived = FALSE
            GROUP BY c.id
            ORDER BY c.started_at DESC
        """, (str(user_id), str(user_id), str(user_id)))
        return [dict(r) for r in cursor.fetchall()]


@router.patch("/{conv_id}")
async def rename_conversation(
    conv_id: int,
    body: ConversationRename,
    user_id: UUID = Depends(get_current_user_id),
):
    title = body.title.strip()[:120] or None
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE conversations SET title = %s WHERE id = %s AND user_id = %s RETURNING id",
            (title, conv_id, str(user_id)),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": conv_id, "title": title}


@router.delete("/{conv_id}")
async def archive_conversation(
    conv_id: int,
    user_id: UUID = Depends(get_current_user_id),
):
    """Soft-delete: hide the conversation from the sidebar. Its messages stay in
    the database so the nightly parser and knowledge graph keep referencing them."""
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE conversations SET archived = TRUE WHERE id = %s AND user_id = %s RETURNING id",
            (conv_id, str(user_id)),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": conv_id, "archived": True}
