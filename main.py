import sqlite3
import os
import json
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from openai import OpenAI
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    openai_api_key: str

    class Config:
        env_file = ".env"

# Initialize settings
settings = Settings()

app = FastAPI()

# OpenAI client
client = OpenAI(api_key=settings.openai_api_key)

# ========================== CORS ==========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, specify your exact frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================== DB ==========================
DB_NAME = "journal.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        
        # 1. Journal Entries Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS journal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                valence REAL,                
                arousal REAL,                
                primary_quadrant TEXT,
                cognitive_labels TEXT,       
                created_at TEXT NOT NULL
            )
        """)
        
        # 2. Daily Habits / Metrics Table (NEW)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                journal_id INTEGER UNIQUE,
                sleep_quality TEXT,      -- e.g., Poor, Fair, Good, Excellent
                exercise_type TEXT,      -- e.g., Cardio, Strength, None
                diet_quality TEXT,       -- e.g., Clean, Heavy/Junk, Balanced
                deep_work_hours REAL,    -- Extracted estimate of focused hours
                FOREIGN KEY(journal_id) REFERENCES journal_entries(id)
            )
        """)
        
        # 3. Todos Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                journal_id INTEGER,
                task_description TEXT NOT NULL,
                is_completed INTEGER DEFAULT 0,
                due_date TEXT,
                FOREIGN KEY(journal_id) REFERENCES journal_entries(id)
            )
        """)
        
        # 4. Ideas Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                journal_id INTEGER,
                title TEXT,
                description TEXT,
                tags TEXT,
                FOREIGN KEY(journal_id) REFERENCES journal_entries(id)
            )
        """)
        conn.commit()

init_db()

# ========================== OpenAI parser ==========================
class TodoItem(BaseModel):
    task: str = Field(description="The actionable task extracted from the text.")
    due_date: Optional[str] = Field(description="YYYY-MM-DD format if mentioned, else null.")

class IdeaItem(BaseModel):
    title: str = Field(description="A short, catchy title for the idea.")
    description: str = Field(description="Elaboration of the idea extracted from the text.")
    tags: str = Field(description="Comma separated tags for the idea.")

class HabitMetrics(BaseModel):
    sleep_quality: Optional[str] = Field(description="One of: Poor, Fair, Good, Excellent. Null if not mentioned.")
    exercise_type: Optional[str] = Field(description="One of: Light Cardio, Heavy Cardio, Light Strength, Heavy Strength, None. None if not mentioned.")
    diet_quality: Optional[str] = Field(description="One of: Clean, Junk/Heavy, Carbs Centered, Meat and Vegetable cnetered. Null if not mentioned.")
    deep_work_hours: Optional[float] = Field(description="Estimated hours of deep focus mentioned. Null if not mentioned.")

class EmotionalAnalysis(BaseModel):
    valence: float = Field(description="Float from -1.0 (Unpleasant/Sad) to +1.0 (Pleasant/Happy).")
    arousal: float = Field(description="Float from -1.0 (Low Energy/Lethargic) to +1.0 (High Energy/Frantic).")
    primary_quadrant: str = Field(description="Must be one of: Peak Performance, High-Stress, Low-Energy, Recovery & Clarity")
    cognitive_labels: List[str] = Field(description="List of 1-3 specific emotional words describing the state.")

class JournalParserResponse(BaseModel):
    todos: List[TodoItem]
    ideas: List[IdeaItem]
    habits: HabitMetrics
    emotions: EmotionalAnalysis

# ---------------------------------------------------------
# BACKGROUND TASK LOGIC
# ---------------------------------------------------------

def process_brain_dump_background(journal_id: int, content: str):
    print(f"[Background Task] Triggered parsing for Journal ID {journal_id}")
    
    try:
        # 1. Call OpenAI using Structured Outputs
        completion = client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert productivity analyst. Extract todos, ideas, habit metrics, and emotional state from the user's daily journal entry. If a field is not mentioned, return null for that field."},
                {"role": "user", "content": content}
            ],
            response_format=JournalParserResponse,
        )
        
        parsed_data = completion.choices[0].message.parsed
        
        # 2. Save the extracted data back to SQLite
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            
            # Update journal entry with emotions
            cursor.execute("""
                UPDATE journal_entries 
                SET valence = ?, arousal = ?, primary_quadrant = ?, cognitive_labels = ?
                WHERE id = ?
            """, (
                parsed_data.emotions.valence,
                parsed_data.emotions.arousal,
                parsed_data.emotions.primary_quadrant,
                json.dumps(parsed_data.emotions.cognitive_labels),
                journal_id
            ))
            
            # Insert Habits
            cursor.execute("""
                INSERT INTO daily_habits (journal_id, sleep_quality, exercise_type, diet_quality, deep_work_hours)
                VALUES (?, ?, ?, ?, ?)
            """, (
                journal_id,
                parsed_data.habits.sleep_quality,
                parsed_data.habits.exercise_type,
                parsed_data.habits.diet_quality,
                parsed_data.habits.deep_work_hours
            ))
            
            # Insert Todos
            for todo in parsed_data.todos:
                cursor.execute(
                    "INSERT INTO todos (journal_id, task_description, due_date) VALUES (?, ?, ?)",
                    (journal_id, todo.task, todo.due_date)
                )
                
            # Insert Ideas
            for idea in parsed_data.ideas:
                cursor.execute(
                    "INSERT INTO ideas (journal_id, title, description, tags) VALUES (?, ?, ?, ?)",
                    (journal_id, idea.title, idea.description, idea.tags)
                )
                
            conn.commit()
            print(f"[Background Task] Successfully parsed and saved data for Journal ID {journal_id}")
            
    except Exception as e:
        print(f"[Background Task] Error processing Journal ID {journal_id}: {e}")

# ==================================== Endpoints ================================================

class JournalSubmit(BaseModel):
    content: str

@app.post("/api/journal")
async def create_journal(entry: JournalSubmit, background_tasks: BackgroundTasks):
    if not entry.content.strip():
        raise HTTPException(status_code=400, detail="Journal content cannot be empty")
        
    current_time = datetime.now().isoformat()
    
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO journal_entries (content, created_at) VALUES (?, ?)",
            (entry.content, current_time)
        )
        journal_id = cursor.lastrowid
        conn.commit()
    
    # Async LLM parsing
    background_tasks.add_task(process_brain_dump_background, journal_id, entry.content)
    
    return {"status": "success", "journal_id": journal_id, "message": "Journal saved. Processing started."}

@app.get("/api/dashboard")
async def get_dashboard_data():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row  # Allows accessing columns by name
        cursor = conn.cursor()
        
        # Fetch last 7 journal entries with their associated habits
        cursor.execute("""
            SELECT j.*, h.sleep_quality, h.exercise_type, h.diet_quality, h.deep_work_hours 
            FROM journal_entries j
            LEFT JOIN daily_habits h ON j.id = h.journal_id
            ORDER BY j.created_at DESC LIMIT 7
        """)
        entries = [dict(row) for row in cursor.fetchall()]
        
        # Unpack JSON strings for cognitive labels safely
        for entry in entries:
            if entry.get("cognitive_labels"):
                try:
                    entry["cognitive_labels"] = json.loads(entry["cognitive_labels"])
                except:
                    entry["cognitive_labels"] = []
            else:
                entry["cognitive_labels"] = []

        # Fetch recent Todos
        cursor.execute("SELECT * FROM todos ORDER BY id DESC LIMIT 15")
        todos = [dict(row) for row in cursor.fetchall()]
        
        # Fetch recent Ideas
        cursor.execute("SELECT * FROM ideas ORDER BY id DESC LIMIT 15")
        ideas = [dict(row) for row in cursor.fetchall()]
        
    return {
        "entries": entries,
        "todos": todos,
        "ideas": ideas
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
