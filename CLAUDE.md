# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**MindForge AI** — a personal journaling app that uses GPT-4o-mini to parse free-form "brain dumps" into structured data: todos, ideas, habit metrics, and emotional analysis.

## Architecture

Two independently running processes:

| Layer | Stack | Location |
|---|---|---|
| Backend | Python FastAPI + SQLite + OpenAI SDK | `main.py` |
| Frontend | React 19 + Vite + Tailwind CSS v4 | `journal-frontend/` |

The frontend hardcodes `http://127.0.0.1:8000` as the API base — no proxy is configured.

### Data Flow

1. User submits a journal entry → `POST /api/journal` immediately writes raw text to SQLite and returns.
2. A FastAPI `BackgroundTask` calls `client.beta.chat.completions.parse()` (structured outputs) with `gpt-4o-mini`, extracting a `JournalParserResponse` that contains todos, ideas, habit metrics, and emotional valence/arousal.
3. The background task then updates the DB with all extracted data.
4. `GET /api/dashboard` joins `journal_entries` + `daily_habits` and returns the last 7 entries plus the most recent 15 todos and ideas.

### Database Schema (`journal.db`)

- `journal_entries` — raw content, valence/arousal floats, primary_quadrant string, cognitive_labels JSON array
- `daily_habits` — one-to-one with journal_entries; sleep_quality, exercise_type, diet_quality, deep_work_hours
- `todos` — task_description, is_completed, due_date; linked to a journal entry
- `ideas` — title, description, comma-separated tags; linked to a journal entry

### Emotional Model

Emotions are mapped onto a 2D affective space:
- **Valence**: -1.0 (unpleasant) → +1.0 (pleasant)
- **Arousal**: -1.0 (lethargic) → +1.0 (frantic)
- **Quadrants**: `Peak Performance`, `High-Stress`, `Low-Energy`, `Recovery & Clarity`

## Environment Setup

Backend requires a `.env` file at the repo root:
```
OPENAI_API_KEY="sk-..."
```

Loaded automatically by `pydantic_settings.BaseSettings`.

## Running the App

**Backend** (from repo root):
```bash
pip install fastapi uvicorn openai pydantic pydantic-settings
python main.py
# or: uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

**Frontend** (from `journal-frontend/`):
```bash
npm install
npm run dev
```

## Frontend Commands

All run from `journal-frontend/`:

```bash
npm run dev      # dev server with HMR
npm run build    # production build
npm run lint     # ESLint
npm run preview  # preview production build
```
