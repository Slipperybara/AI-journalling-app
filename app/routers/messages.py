from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..auth import get_current_user_id
from ..bot import process_message_background
from ..db import connect
from ..models import MessageCreate


router = APIRouter(prefix="/api/conversations/{conv_id}/messages", tags=["messages"])


@router.get("")
async def get_messages(conv_id: int, user_id: UUID = Depends(get_current_user_id)):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, role, content, created_at
            FROM messages
            WHERE conversation_id = %s AND user_id = %s
            ORDER BY id ASC
        """, (conv_id, str(user_id)))
        return [dict(r) for r in cursor.fetchall()]


@router.post("")
async def post_message(
    conv_id: int,
    msg: MessageCreate,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user_id),
):
    if not msg.content.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM conversations WHERE id = %s AND user_id = %s",
            (conv_id, str(user_id)),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Conversation not found")

        created_at = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO messages (user_id, conversation_id, role, content, created_at)
            VALUES (%s, %s, 'user', %s, %s)
            RETURNING id
        """, (str(user_id), conv_id, msg.content, created_at))
        msg_id = cursor.fetchone()["id"]

    background_tasks.add_task(process_message_background, conv_id, msg.content, user_id)
    return {"id": msg_id, "role": "user", "content": msg.content, "created_at": created_at}
