"""Graph-result digester.

This module does NOT produce user-facing prose. Its output is internal
context handed to the conversational bot (`app.bot.generate_bot_reply`), which
weaves the relevant facts into a reply with the journaling bot's voice and
priorities.

Why split: gpt-4o on raw Neo4j JSON occasionally either dumps the data verbatim
or buries the relevant fact. A focused fact-digester step (specific dates,
counts, valences, labels) leaves the bot one clean string to integrate.
"""
import json

from ..core import client


def synthesize_response(
    user_message: str,
    graph_result: list,
    failed: bool = False,
) -> str:
    """Digest the first 30 graph records into a focused factual summary.
    Output is internal context for the bot — not for the user directly.

    On failed=True (query exhausted retries), returns a short failure signal
    string the bot can phrase appropriately. No LLM call in that path."""
    if failed:
        return "(graph query failed after retries; no factual data available for this question)"

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract and digest facts from a Neo4j query result for use by a "
                    "separate conversational bot. The bot weaves your output into a natural "
                    "reply for the user — your output is INTERNAL CONTEXT, not user-facing prose. "
                    "Do not address the user. Do not be conversational.\n\n"
                    "Read the user's question and the graph data. Produce a focused, factual "
                    "digest of the data that is RELEVANT to answering the question. Be specific: "
                    "include actual dates, names, counts, valence/arousal values, quadrant labels, "
                    "etc. exactly as they appear. Omit irrelevant rows — the bot will only weave "
                    "in what helps the user.\n\n"
                    "Format: short labeled lines or compact prose. No markdown, no headings. "
                    "Length: as long as needed to capture relevant facts, typically 4-12 lines "
                    "for a query with results, shorter if data is sparse. If the data is empty, "
                    "say so plainly (e.g. 'No matching entries in the graph for the last N days')."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User question: {user_message}\n\n"
                    f"Graph data (first 30 records):\n{json.dumps(graph_result[:30], indent=2)}"
                ),
            },
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()
