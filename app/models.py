"""Pydantic schemas for the structured-output parser.

Field(description=...) strings are the LLM instructions — keep them precise.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class TodoItem(BaseModel):
    task: str = Field(
        description=(
            "A concrete, atomic, daily executable taking under 3 hours. "
            "If the user mentions a larger project or goal, break it into separate "
            "sub-tasks each under 3 hours — emit one TodoItem per sub-task. "
            "Never emit a single todo for anything that would take more than 3 hours. "
            "If sub-tasks cannot be inferred, emit a single first-next-action "
            "(e.g. 'Research options for X — 1h'). Skip vague intentions with no clear next action."
        )
    )
    due_date: Optional[str] = Field(description="YYYY-MM-DD if explicitly mentioned, else null.")


class EventItem(BaseModel):
    title: str = Field(description="Short title for this event.")
    description: str = Field(description="One or two sentence elaboration grounded in what the user said.")
    tags: str = Field(description="Comma-separated tags. May be empty string.")
    event_type: str = Field(description="Must be one of: idea, location, milestone, media")


class EmotionalAnalysis(BaseModel):
    valence: float = Field(description="Float -1.0 (Unpleasant) to +1.0 (Pleasant).")
    arousal: float = Field(description="Float -1.0 (Low Energy/Lethargic) to +1.0 (High Energy/Frantic).")
    primary_quadrant: str = Field(description="Must be one of: Peak Performance, High-Stress, Low-Energy, Recovery & Clarity")
    cognitive_labels: List[str] = Field(description="1-3 specific emotional words describing the state. Empty list only if message is purely factual with zero affect.")
    cognitive_triggers: List[str] = Field(description="Specific events, thoughts or situations the user said triggered their state. Empty list if none mentioned.")
    social_interactions: List[str] = Field(description="People, roles, or social contexts mentioned (e.g., 'mom', 'standup with team'). Empty list if none mentioned.")


class HealthMetrics(BaseModel):
    sleep_quality: Optional[str] = Field(description="One of: Poor, Fair, Good, Excellent. Null if not mentioned.")
    exercise_type: Optional[str] = Field(description="One of: Light Cardio, Heavy Cardio, Light Strength, Heavy Strength, None. Null if not mentioned.")
    diet_quality: Optional[str] = Field(description="One of: Clean, Junk/Heavy, Carbs Centered, Meat and Vegetable centered. Null if not mentioned.")
    somatic_sensations: List[str] = Field(description="Physical sensations mentioned (e.g., fatigue, heatiness, dampness, soreness, headache). Empty list if none.")
    physical_performance: Optional[str] = Field(description="Performance metrics mentioned, like '30 pushups' or 'IPPT score 78'. Null if none.")
    supplements: List[str] = Field(description="Supplements taken, e.g., creatine, vitamin D, fish oil. Empty list if none mentioned.")


class ProductivityMetrics(BaseModel):
    deep_work_hours: Optional[float] = Field(description="Hours of focused deep work the user mentioned. Null if not mentioned.")
    shallow_work_hours: Optional[float] = Field(description="Hours of shallow / admin / meeting work. Null if not mentioned.")
    time_block_adherence: Optional[str] = Field(description="How well the user stuck to planned time blocks. One of: High, Medium, Low. Null if not mentioned.")
    cognitive_load: Optional[str] = Field(description="Subjective cognitive load. One of: High, Medium, Low. Null if not mentioned.")
    friction_points: List[str] = Field(description="Obstacles, blockers, or sources of friction the user mentioned. Empty list if none.")


class JournalParserResponse(BaseModel):
    todos: List[TodoItem]
    events: List[EventItem]
    emotions: EmotionalAnalysis
    health: HealthMetrics
    productivity: ProductivityMetrics


class MessageCreate(BaseModel):
    content: str
