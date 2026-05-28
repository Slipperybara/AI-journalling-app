"""Heavy DB agent: Cypher generation, self-correction, and result evaluation."""
import json

from ..core import client
from ..graph_schema import ONTOLOGY_SCHEMA


def generate_cypher(user_message: str, eval_hint: str = "") -> str:
    """Generate a Cypher query for the user's analytical question."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a Neo4j Cypher expert. Generate a single Cypher query to answer "
                "the user's question about their personal journal history. "
                "Use ONLY the labels and relationship types defined in the schema below. "
                "Prefer MATCH patterns over raw property filters where possible. "
                "Return ONLY the raw Cypher query — no explanation, no markdown fences.\n\n"
                + ONTOLOGY_SCHEMA
            ),
        },
        {"role": "user", "content": user_message},
    ]
    if eval_hint:
        messages.append({
            "role": "user",
            "content": f"Previous query was incomplete. Broaden it: {eval_hint}",
        })

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0,
    )
    return response.choices[0].message.content.strip()


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
                    f"Query result (first 20 records):\n{json.dumps(graph_result[:20], indent=2)}\n\n"
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
