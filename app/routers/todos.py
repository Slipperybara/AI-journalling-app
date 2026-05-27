"""CRUD endpoints for individual todos."""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import connect


router = APIRouter(prefix="/api/todos", tags=["todos"])


class TodoCreate(BaseModel):
    task_description: str
    day: str
    due_date: str | None = None


@router.get("/{day}")
async def get_todos_for_day(day: str):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, day, task_description, is_completed, due_date,
                   created_at, fulfilled_at, source_day
            FROM todos
            WHERE day = ?
            ORDER BY id ASC
        """, (day,))
        return [dict(r) for r in cursor.fetchall()]


@router.post("")
async def create_todo(body: TodoCreate):
    now = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO todos (day, task_description, due_date, created_at, is_completed)
            VALUES (?, ?, ?, ?, 0)
        """, (body.day, body.task_description, body.due_date, now))
        todo_id = cursor.lastrowid
    return {
        "id": todo_id,
        "day": body.day,
        "task_description": body.task_description,
        "due_date": body.due_date,
        "is_completed": 0,
        "created_at": now,
        "fulfilled_at": None,
        "source_day": None,
    }


@router.patch("/{todo_id}/complete")
async def complete_todo(todo_id: int):
    now = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE todos SET is_completed = 1, fulfilled_at = ? WHERE id = ?",
            (now, todo_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Todo not found")
    return {"id": todo_id, "is_completed": 1, "fulfilled_at": now}


@router.patch("/{todo_id}/uncomplete")
async def uncomplete_todo(todo_id: int):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE todos SET is_completed = 0, fulfilled_at = NULL WHERE id = ?",
            (todo_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Todo not found")
    return {"id": todo_id, "is_completed": 0, "fulfilled_at": None}


@router.delete("/{todo_id}")
async def delete_todo(todo_id: int):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Todo not found")
    return {"deleted": todo_id}
