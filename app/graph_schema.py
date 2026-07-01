"""Graph ontology constants. ONTOLOGY_SCHEMA is injected into LLM prompts.

Phase 2 multi-tenant: EVERY node — domain and reference — carries a
`user_id` UUID property. The LangGraph Cypher-generator MUST filter every
label pattern by `user_id = $user_id`. Reference nodes are per-user (not
global) so traversals through them stay inside the user's subgraph by
construction; this closes the leak where a shared `(:EmotionQuadrant)`
could bridge two users' EmotionStates.

`validate_user_id_scoping` is a regex-based guardrail that the LangGraph
db_executor calls before running any generated query; it catches the
common failure mode (label pattern without user_id in its props body and
no WHERE alias.user_id filter).
"""
import re

from . import tracking_catalog

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

# Labels that carry user_id. Used by the LangGraph guardrail to validate
# generated Cypher includes a user_id filter on every reference to these.
USER_SCOPED_LABELS = (
    "Day", "EmotionState", "HealthState", "Event", "Topic", "Goal",
    "EmotionQuadrant", "SleepQuality", "ExerciseType", "DietQuality", "Category",
) + tracking_catalog.preset_node_labels()

ONTOLOGY_SCHEMA = """
Graph Ontology — ONLY use these labels and relationship types.

CRITICAL MULTI-TENANT SCOPING RULE:
Every node label below carries a `user_id` UUID property. Every Cypher query
you generate MUST filter EVERY label pattern by `user_id = $user_id`. There
are NO globally-shared nodes — even EmotionQuadrant, SleepQuality, etc. are
per-user. The user_id is supplied as the parameter $user_id; do not hardcode
it. Examples of correct patterns:

  MATCH (d:Day {user_id: $user_id, date: $day})
  MATCH (es:EmotionState {user_id: $user_id})-[:IN_QUADRANT]->(q:EmotionQuadrant {user_id: $user_id})
  MATCH (e:Event) WHERE e.user_id = $user_id

Queries that omit `user_id` on any label will be rejected.

Node Labels and key properties:
- Day: user_id, date (string YYYY-MM-DD), deep_work_hours (float), shallow_work_hours (float), time_block_adherence (string: High|Medium|Low), cognitive_load (string: High|Medium|Low), friction_points (list of strings)
- EmotionState: user_id, valence (float -1 to 1), arousal (float -1 to 1), cognitive_labels (list), cognitive_triggers (list), social_interactions (list)
- EmotionQuadrant: user_id, name (string: Peak Performance|High-Stress|Low-Energy|Recovery & Clarity)
- HealthState: user_id, somatic_sensations (list), physical_performance (string), supplements (list)
- SleepQuality: user_id, level (string: Poor|Fair|Good|Excellent)
- ExerciseType: user_id, name (string: Light Cardio|Heavy Cardio|Light Strength|Heavy Strength|None)
- DietQuality: user_id, type (string: Clean|Junk/Heavy|Carbs Centered|Meat and Vegetable centered)
- Event: user_id, canonical_id (string), title (string), event_type (string: idea|location|milestone|media), description (string), tags (list)
- Topic: user_id, name (string, normalised lowercase)
- Category: user_id, name (string)
- Goal: user_id, name (string), discovered_on (string YYYY-MM-DD)

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
""" + tracking_catalog.ontology_block()


def validate_user_id_scoping(query: str) -> str | None:
    """Regex check that every user-scoped label pattern in `query` filters by
    user_id. Returns None if valid, otherwise an error string suitable for
    feeding into the LangGraph self-correction loop.

    Heuristic — checks each label pattern of the form `(alias:Label …)` and
    requires either `user_id:` inside the property body OR a later
    `alias.user_id =` reference somewhere in the query.
    """
    for label in USER_SCOPED_LABELS:
        # Match either `(alias:Label` or `(:Label`, then capture node body
        # up to the matching `)`. We don't try to balance nested parens —
        # node bodies in valid Cypher don't have them.
        pattern = re.compile(
            rf"\((?P<alias>[A-Za-z_]\w*)?:{label}\b(?P<body>[^()]*)\)",
        )
        for m in pattern.finditer(query):
            alias = m.group("alias") or ""
            body = m.group("body") or ""
            has_inline = "user_id" in body
            has_where = False
            if alias:
                where_pat = re.compile(rf"\b{re.escape(alias)}\.user_id\b")
                if where_pat.search(query):
                    has_where = True
            if not (has_inline or has_where):
                return (
                    f"Label `:{label}` appears without a `user_id` filter. "
                    f"Every label pattern MUST include `{{user_id: $user_id}}` "
                    f"in its node body OR a `<alias>.user_id = $user_id` filter "
                    f"in a WHERE clause."
                )
    return None
