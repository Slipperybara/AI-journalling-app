"""Heavy DB agent: Cypher generation, self-correction, and result evaluation.

Generation is ReAct-style: each iteration receives the full search history
(prior queries + their result samples + the evaluator's verdicts and hints)
and decides its next move — broaden, pivot, refine, or deepen — instead of
writing each query in isolation.
"""
import json

from ..core import client
from ..graph_schema import ONTOLOGY_SCHEMA


def generate_cypher(
    user_message: str, search_history: list[dict] | None = None
) -> str:
    """Generate the next Cypher query in an exploration loop.

    `search_history` is a chronological list of prior attempts; each entry:
        {query, result_count, result_sample, eval_passed, eval_hint}
    On the first iteration the list is empty.
    """
    history_block = _format_search_history(search_history or [])
    user_block = (
        f"User question: {user_message}\n\n"
        f"{history_block}\n\n"
        "Write the next Cypher query."
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are exploring the user's personal journal history graph in Neo4j to "
                    "answer their question. You may have made previous attempts — review them "
                    "and decide your next move.\n\n"
                    "Possible moves:\n"
                    "  - BROADEN a query if the previous returned too few or empty results.\n"
                    "  - PIVOT to a different angle if results were off-target.\n"
                    "  - REFINE if you're on the right track but need different fields, "
                    "grouping, or ordering.\n"
                    "  - DEEPEN by joining additional labels for richer context.\n\n"
                    "Use ONLY the labels and relationship types defined in the schema below. "
                    "Prefer MATCH patterns over raw property filters. Return ONLY the raw "
                    "Cypher query — no explanation, no markdown fences, no commentary.\n\n"
                    + ONTOLOGY_SCHEMA
                ),
            },
            {"role": "user", "content": user_block},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def _format_search_history(history: list[dict]) -> str:
    if not history:
        return "Previous attempts: (none yet — this is your first attempt)"
    lines = ["Previous attempts (oldest first):"]
    for i, h in enumerate(history, 1):
        verdict = "SATISFIED" if h.get("eval_passed") else "NOT SATISFIED"
        hint = (h.get("eval_hint") or "").strip() or "(no hint)"
        sample = h.get("result_sample") or []
        sample_str = (
            json.dumps(sample[:3], indent=2, default=str) if sample else "(empty)"
        )
        lines.append(
            f"\nAttempt {i}:\n"
            f"  Query: {(h.get('query') or '').strip()}\n"
            f"  Records returned: {h.get('result_count', 0)}\n"
            f"  Sample (up to 3): {sample_str}\n"
            f"  Evaluator: {verdict} — {hint}"
        )
    return "\n".join(lines)


def correct_cypher(query: str, error: str) -> str:
    """Rewrite a failing Cypher query given its error message."""
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a Cypher expert. Fix the failing query. "
                    "Use ONLY labels and relationships defined in the schema below. "
                    "Return ONLY the corrected Cypher query — no explanation, no markdown fences.\n\n"
                    + ONTOLOGY_SCHEMA
                ),
            },
            {
                "role": "user",
                "content": f"Query:\n{query}\n\nError:\n{error}\n\nRewrite it correctly.",
            },
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def evaluate_result(user_message: str, graph_result: list) -> dict:
    """Check whether graph_result fully answers user_message.
    Returns {"satisfied": bool, "hint": str}."""
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are evaluating whether a Neo4j query result fully answers a user's question. "
                    "Respond with JSON only: "
                    "{\"satisfied\": true | false, \"hint\": \"<optional: what a better query would retrieve>\"}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User question: {user_message}\n\n"
                    f"Query result (first 20 records):\n{json.dumps(graph_result[:20], indent=2, default=str)}\n\n"
                    "Does this result fully answer the question?"
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError:
        return {"satisfied": True, "hint": ""}
