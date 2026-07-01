"""Catalog of preset tracking fields — the single source of truth.

Users customize what JAI tracks for them (see `app/tracking.py`). Preset fields
come from this fixed catalog and each becomes its OWN Neo4j node label
(`:Hydration`, `:Meditation`, …); one node per (user, day) holds that day's
reading. Custom user fields are NOT here — they ride the existing Event pipeline.

Everything that must stay in lock-step with the catalog derives from it here so
they never drift:
  - `preset_node_labels()` → `graph_schema.USER_SCOPED_LABELS` + guardrail
  - `ontology_block()`     → spliced into `graph_schema.ONTOLOGY_SCHEMA`
  - `index_specs()`        → `graph_db.init_graph`
  - `by_key` / `value_hint` → parser addendum + extraction filtering + projection

`value_hint` is the per-field extraction contract the parser sees — it's how we
get "typed values per field" without a dynamic Pydantic model. `node_label` must
be a bare, valid Cypher label (letters only, PascalCase) so it can be safely
f-string-interpolated on the write path.
"""

# key, label (UI), node_label (Neo4j), value_hint (parser extraction contract)
PRESET_CATALOG: list[dict] = [
    {
        "key": "hydration",
        "label": "Hydration",
        "node_label": "Hydration",
        "value_hint": "water / fluid intake, e.g. '6 glasses', '2L', 'barely drank water'",
    },
    {
        "key": "meditation",
        "label": "Meditation",
        "node_label": "Meditation",
        "value_hint": "meditation or breathwork done, e.g. '10 min', 'morning session', 'skipped'",
    },
    {
        "key": "gratitude",
        "label": "Gratitude",
        "node_label": "Gratitude",
        "value_hint": "what the user felt grateful or thankful for; a short phrase",
    },
    {
        "key": "outdoors",
        "label": "Time outdoors",
        "node_label": "Outdoors",
        "value_hint": "time spent outdoors / in nature, e.g. '30 min walk in the park', 'inside all day'",
    },
    {
        "key": "reading",
        "label": "Reading",
        "node_label": "Reading",
        "value_hint": "what and roughly how much the user read, e.g. '20 pages of a novel', 'read before bed'",
    },
    {
        "key": "screen_time",
        "label": "Screen time",
        "node_label": "ScreenTime",
        "value_hint": "time on phone / screens, e.g. '5 hours', 'doomscrolled a lot', 'low screen day'",
    },
    {
        "key": "social",
        "label": "Social connection",
        "node_label": "Social",
        "value_hint": "meaningful social contact, e.g. 'dinner with friends', 'felt isolated'",
    },
    {
        "key": "energy",
        "label": "Energy",
        "node_label": "Energy",
        "value_hint": "overall energy level, e.g. 'Low', 'Moderate', 'High', or a short descriptor",
    },
    {
        "key": "stress",
        "label": "Stress",
        "node_label": "Stress",
        "value_hint": "overall stress level, e.g. 'Low', 'Moderate', 'High', or what drove it",
    },
    {
        "key": "caffeine",
        "label": "Caffeine",
        "node_label": "Caffeine",
        "value_hint": "caffeine intake, e.g. '2 coffees', 'no caffeine', 'green tea in the afternoon'",
    },
    {
        "key": "alcohol",
        "label": "Alcohol",
        "node_label": "Alcohol",
        "value_hint": "alcohol intake, e.g. '2 beers', 'none', 'glass of wine with dinner'",
    },
    {
        "key": "creativity",
        "label": "Creative time",
        "node_label": "Creativity",
        "value_hint": "creative activity, e.g. 'played guitar for an hour', 'sketched', 'wrote'",
    },
]

BY_KEY: dict[str, dict] = {e["key"]: e for e in PRESET_CATALOG}
_LABEL_BY_KEY: dict[str, str] = {e["key"]: e["node_label"] for e in PRESET_CATALOG}


def preset_keys() -> list[str]:
    return [e["key"] for e in PRESET_CATALOG]


def is_preset_key(key: str) -> bool:
    return key in BY_KEY


def node_label_for(key: str) -> str | None:
    """Neo4j label for a preset key, or None if unknown. Only keys in the
    catalog resolve, so the returned label is always a safe (allowlisted)
    identifier for f-string interpolation on the write path."""
    return _LABEL_BY_KEY.get(key)


def preset_node_labels() -> tuple[str, ...]:
    return tuple(e["node_label"] for e in PRESET_CATALOG)


def index_specs() -> list[tuple[str, str]]:
    """(label, property) pairs for `graph_db.init_graph` composite indexes."""
    return [(e["node_label"], "value") for e in PRESET_CATALOG]


def ontology_block() -> str:
    """Section describing tracked-field labels, spliced into ONTOLOGY_SCHEMA so
    the Cypher generator knows they exist and the guardrail scopes them."""
    lines = [
        "",
        "User-customizable Tracked Field labels (each node is ONE day's reading of that",
        "field for the user; all share the same shape). Like every label, they carry",
        "`user_id` and MUST be filtered by `user_id = $user_id`:",
    ]
    for e in PRESET_CATALOG:
        lines.append(f"- {e['node_label']}: user_id, value (string), note (string)")
    lines.append("Relationship for all of the above:")
    lines.append("- (Day)-[:TRACKED]->(<any Tracked Field label above>)")
    return "\n".join(lines) + "\n"
