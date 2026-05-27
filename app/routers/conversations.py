from datetime import datetime

from fastapi import APIRouter

from ..db import connect


router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.post("")
async def create_conversation():
    started_at = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO conversations (started_at) VALUES (?)", (started_at,))
        conv_id = cursor.lastrowid
    return {"id": conv_id, "started_at": started_at}


@router.get("")
async def list_conversations():
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.started_at,
                   COUNT(m.id) AS message_count,
                   MAX(m.created_at) AS last_message_at,
                   (
                       SELECT content FROM messages
                       WHERE conversation_id = c.id AND role = 'user'
                       ORDER BY id ASC LIMIT 1
                   ) AS first_user_message
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            GROUP BY c.id
            ORDER BY c.started_at DESC
        """)
        return [dict(r) for r in cursor.fetchall()]
