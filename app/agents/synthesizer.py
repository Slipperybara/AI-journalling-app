"""Graph-result digester (advisor mode).

This module does NOT produce user-facing prose. Its output is internal
context handed to the conversational bot (`app.bot.generate_bot_reply`), which
weaves the relevant pieces into a reply with the journaling bot's voice.

Output is structured into three labeled sections:
  - FACTS: the data points relevant to answering the question.
  - OBSERVATIONS: 1-2 short lines noting patterns or contrasts visible in the
    records (advisor's lens, not user-facing).
  - SUGGESTIONS: 1-2 short, grounded candidate suggestions. These are
    *candidates* — the bot judges whether to surface them based on relevance.

Why split: gpt-4o on raw Neo4j JSON occasionally either dumps the data verbatim
or buries the relevant fact. A focused digester step (FACTS + OBSERVATIONS +
SUGGESTIONS) gives the bot one clean string to integrate and lets the bot
decide whether an actionable improvement is worth surfacing.
"""
import json

from ..core import client


def synthesize_response(
    user_message: str,
    graph_result: list,
    failed: bool = False,
) -> str:
    """Digest the first 30 graph records into FACTS/OBSERVATIONS/SUGGESTIONS.
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
                    "You extract facts and propose suggestion candidates from a Neo4j query "
                    "result for use by a separate conversational bot. The bot weaves your "
                    "output into a natural reply for the user — your output is INTERNAL "
                    "CONTEXT, not user-facing prose. Do not address the user. Do not be "
                    "conversational.\n\n"
                    "Read the user's question and the graph data. Produce three labeled "
                    "sections in this exact order:\n\n"
                    "FACTS:\n"
                    "  Short labeled lines or compact prose. Include actual dates, names, "
                    "counts, valence/arousal values, quadrant labels, etc. as they appear in "
                    "the data. Omit irrelevant rows. If the data is empty, say so plainly "
                    "(e.g. 'No matching entries in the graph for the last N days').\n\n"
                    "OBSERVATIONS:\n"
                    "  1-2 short lines noting patterns, contrasts, or trends visible in the "
                    "records. This is your advisor lens. Leave blank ('OBSERVATIONS: (none)') "
                    "if the data shows nothing notable.\n\n"
                    "SUGGESTIONS:\n"
                    "  1-2 short suggestion CANDIDATES grounded in the FACTS — concrete, not "
                    "generic. Examples of good: 'The 3 high-stress days all followed nights "
                    "without sleep mentioned — worth tracking sleep this week.' / 'You've hit "
                    "Peak Performance after exercise 4 of 5 times — light cardio in the "
                    "morning may be worth the bet today.' Examples of bad (too generic): "
                    "'Try to sleep more.' / 'Exercise helps.' Leave blank "
                    "('SUGGESTIONS: (none)') if the data doesn't justify a recommendation. "
                    "The downstream bot will judge whether to surface — your job is to "
                    "propose candidates, not to filter.\n\n"
                    "No markdown, no headings beyond the three section labels above. Total "
                    "length: typically 6-15 lines."
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
