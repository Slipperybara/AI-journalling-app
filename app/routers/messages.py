from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..bot import process_message_background
from ..db import connect
from ..models import MessageCreate


router = APIRouter(prefix="/api/conversations/{conv_id}/messages", tags=["messages"])


@router.get("")
async def get_messages(conv_id: int):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, role, content, created_at
            FROM messages
            WHERE conversation_id = %s
            ORDER BY id ASC
        """, (conv_id,))
        return [dict(r) for r in cursor.fetchall()]


@router.post("")
async def post_message(conv_id: int, msg: MessageCreate, background_tasks: BackgroundTasks):
    if not msg.content.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM conversations WHERE id = %s", (conv_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Conversation not found")

        created_at = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO messages (conversation_id, role, content, created_at)
            VALUES (%s, 'user', %s, %s)
            RETURNING id
        """, (conv_id, msg.content, created_at))
        msg_id = cursor.fetchone()["id"]

    background_tasks.add_task(process_message_background, conv_id, msg.content)
    return {"id": msg_id, "role": "user", "content": msg.content, "created_at": created_at}
