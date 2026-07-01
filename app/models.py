"""Pydantic schemas for the structured-output parser.

Field(description=...) strings are the LLM instructions — keep them precise.

Todos and goals are user-managed only — the parser no longer emits them.
Events still extract `contributes_to_goals` so the LLM can link days of work
back to the user's manually-curated goals list.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class EventItem(BaseModel):
    title: str = Field(description="Short title for this event.")
    description: str = Field(description="One or two sentence elaboration grounded in what the user said.")
    tags: str = Field(description="Comma-separated tags. May be empty string.")
    event_type: str = Field(description="Must be one of: idea, location, milestone, media")
    topics: List[str] = Field(
        default_factory=list,
        description=(
            "1-3 specific conceptual topic tags for this event. "
            "Use precise terms, e.g. ['LLMs', 'RAG'], ['Algorithm Practice'], ['System Design']. "
            "Empty list if the event has no clear intellectual or skill domain."
        ),
    )
    contributes_to_goals: List[str] = Field(
        default_factory=list,
        description=(
            "Names of tracked goals this event directly contributes toward. "
            "Only include names that exactly match a goal from the provided goals list. "
            "Empty list if none match."
        ),
    )


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


class TrackedFieldReading(BaseModel):
    field_key: str = Field(description="Exact key of a tracked field from the provided list. Do not invent keys.")
    value: str = Field(description="Short reading for this field for the day, following that field's described format.")
    note: Optional[str] = Field(default=None, description="Optional brief context grounded in what the user said. Null if none.")


class JournalParserResponse(BaseModel):
    events: List[EventItem]
    emotions: EmotionalAnalysis
    health: HealthMetrics
    productivity: ProductivityMetrics
    tracked_fields: List[TrackedFieldReading] = Field(
        default_factory=list,
        description=(
            "Readings for the user's custom tracked fields — ONLY the ones actually "
            "mentioned today, using ONLY keys from the provided tracked-fields list. "
            "Empty list if none are mentioned or no list is provided."
        ),
    )


class MessageCreate(BaseModel):
    content: str
