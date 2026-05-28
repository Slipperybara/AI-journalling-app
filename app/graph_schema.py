"""Graph ontology constants. ONTOLOGY_SCHEMA is injected into LLM prompts."""

EMOTION_QUADRANTS = [
    "Peak Performance",
    "High-Stress",
    "Low-Energy",
    "Recovery & Clarity",
]

SLEEP_QUALITIES = ["Poor", "Fair", "Good", "Excellent"]

EXERCISE_TYPES = [
    "Light Cardio",
    "Heavy Cardio",
    "Light Strength",
    "Heavy Strength",
    "None",
]

DIET_QUALITIES = [
    "Clean",
    "Junk/Heavy",
    "Carbs Centered",
    "Meat and Vegetable centered",
]

ONTOLOGY_SCHEMA = """
Graph Ontology — ONLY use these labels and relationship types.

Node Labels and key properties:
- Day: date (string YYYY-MM-DD, primary key), deep_work_hours (float), shallow_work_hours (float), time_block_adherence (string: High|Medium|Low), cognitive_load (string: High|Medium|Low), friction_points (list of strings)
- EmotionState: valence (float -1 to 1), arousal (float -1 to 1), cognitive_labels (list), cognitive_triggers (list), social_interactions (list)
- EmotionQuadrant: name (string: Peak Performance|High-Stress|Low-Energy|Recovery & Clarity)
- HealthState: somatic_sensations (list), physical_performance (string), supplements (list)
- SleepQuality: level (string: Poor|Fair|Good|Excellent)
- ExerciseType: name (string: Light Cardio|Heavy Cardio|Light Strength|Heavy Strength|None)
- DietQuality: type (string: Clean|Junk/Heavy|Carbs Centered|Meat and Vegetable centered)
- Event: canonical_id (string), title (string), event_type (string: idea|location|milestone|media), description (string), tags (list)
- Topic: name (string, normalised lowercase)
- Category: name (string)
- Goal: name (string), discovered_on (string YYYY-MM-DD)

Relationship Types:
- (Day)-[:NEXT_DAY]->(Day)
- (Day)-[:HAD_EMOTION]->(EmotionState)
- (EmotionState)-[:IN_QUADRANT]->(EmotionQuadrant)
- (Day)-[:HAD_HEALTH]->(HealthState)
- (HealthState)-[:HAD_SLEEP]->(SleepQuality)
- (HealthState)-[:HAD_EXERCISE]->(ExerciseType)
- (HealthState)-[:HAD_DIET]->(DietQuality)
- (Day)-[:HAD_EVENT]->(Event)
- (Event)-[:INVOLVES]->(Topic)
- (Topic)-[:BELONGS_TO]->(Category)
- (Event)-[:CONTRIBUTES_TO]->(Goal)
"""
