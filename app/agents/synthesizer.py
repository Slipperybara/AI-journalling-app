"""Lightweight synthesizer: converts raw graph results into a conversational reply."""
import json

from ..core import client


def synthesize_response(
    user_message: str,
    graph_result: list,
    sqlite_context: str,
    failed: bool = False,
) -> str:
    """Format a natural language answer from graph data.
    If failed=True, returns a graceful apology without an LLM call."""
    if failed:
        return (
            "I couldn't retrieve that from your history right now — "
            "the query ran into trouble. Try rephrasing or being more specific."
        )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are MindForge, a warm personal journaling companion. "
                    "Answer the user's question using the graph data provided. "
                    "Be concise (2-4 sentences), conversational, and specific — cite actual values "
                    "from the data where relevant. "
                    "If the graph data is empty or returns no records, say so honestly. "
                    "Do not use bullets, headings, or markdown."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question: {user_message}\n\n"
                    f"Graph data:\n{json.dumps(graph_result[:30], indent=2)}\n\n"
                    f"Today's context:\n{sqlite_context}"
                ),
            },
        ],
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()
