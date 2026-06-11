"""LangGraph orchestration: routes each user message to the journaling bot
or the analytical Cypher pipeline, then writes the final reply to Postgres.

Analytical path: router -> cypher_agent -> validator (user_id scoping) ->
db_executor -> (self-correct loop) -> evaluator -> (re-query loop) ->
synthesizer. The synthesizer digests the Neo4j records into a factual
summary, then hands off to generate_bot_reply so the user-facing reply
keeps the bot's voice.

Phase 2 multi-tenant: `user_id` is in GraphState and threaded into every
node — the bot calls receive it, and the Cypher executor passes `$user_id`
as a parameter so the LLM-generated queries that reference `$user_id` (per
the ONTOLOGY_SCHEMA contract) resolve correctly.
"""
from operator import add
from typing import Annotated, Any, TypedDict
from uuid import UUID

from langgraph.graph import END, StateGraph

from .agents.cypher_agent import correct_cypher, evaluate_result, generate_cypher
from .agents.synthesizer import synthesize_response
from .bot import generate_bot_reply, store_assistant_message
from .core import client
from .graph_db import graph_connect
from .graph_schema import validate_user_id_scoping


class GraphState(TypedDict):
    message: str
    conversation_id: int
    user_id: str              # UUID string
    intent: str               # "journaling" | "analytical"
    sqlite_context: str       # synthesizer facts (legacy field name kept)
    cypher_query: str
    graph_result: Any         # list of dicts from Neo4j
    query_error: str          # empty string when no error
    retry_count: int          # Cypher self-correction attempts (syntax + scoping)
    eval_retry_count: int     # evaluation broadening attempts (semantic)
    eval_passed: bool
    eval_hint: str
    search_history: Annotated[list, add]
    final_response: str


# ── Node functions ──────────────────────────────────────────────────────────

def _router_node(state: GraphState) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify the message as 'journaling' (user sharing info about their day) "
                    "or 'analytical' (user asking a question about patterns, history, or trends). "
                    "If the message contains ANY analytical intent, respond 'analytical'. "
                    "Respond with exactly one word: journaling or analytical."
                ),
            },
            {"role": "user", "content": state["message"]},
        ],
        temperature=0,
    )
    intent = response.choices[0].message.content.strip().lower()
    if intent not in ("journaling", "analytical"):
        intent = "journaling"
    return {"intent": intent}


def _bot_node(state: GraphState) -> dict:
    # Journaling needs no graph work beyond routing. The reply is generated
    # outside the graph (streamed by the endpoint, or by process_message).
    return {}


def _cypher_agent_node(state: GraphState) -> dict:
    cypher = generate_cypher(
        state["message"],
        search_history=state.get("search_history", []),
    )
    return {"cypher_query": cypher, "query_error": ""}


def _db_executor_node(state: GraphState) -> dict:
    # Guardrail: every label pattern in the generated Cypher MUST scope by
    # user_id. If the LLM forgot, surface it as a query_error so the
    # self-correct loop rewrites the query.
    scoping_error = validate_user_id_scoping(state["cypher_query"])
    if scoping_error:
        return {
            "graph_result": [],
            "query_error": scoping_error,
            "retry_count": state.get("retry_count", 0) + 1,
        }
    try:
        with graph_connect() as session:
            result = session.run(
                state["cypher_query"],
                user_id=state["user_id"],
            )
            records = [dict(r) for r in result]
        return {"graph_result": records, "query_error": ""}
    except Exception as exc:
        return {
            "graph_result": [],
            "query_error": str(exc),
            "retry_count": state.get("retry_count", 0) + 1,
        }


def _self_correct_node(state: GraphState) -> dict:
    fixed = correct_cypher(state["cypher_query"], state["query_error"])
    return {"cypher_query": fixed, "query_error": ""}


def _evaluator_node(state: GraphState) -> dict:
    verdict = evaluate_result(state["message"], state.get("graph_result", []))
    eval_passed = verdict.get("satisfied", True)
    eval_hint = verdict.get("hint", "")
    graph_result = state.get("graph_result", []) or []
    attempt = {
        "query": state.get("cypher_query", ""),
        "result_count": len(graph_result),
        "result_sample": graph_result[:3],
        "eval_passed": eval_passed,
        "eval_hint": eval_hint,
    }
    return {
        "eval_passed": eval_passed,
        "eval_hint": eval_hint,
        "eval_retry_count": state.get("eval_retry_count", 0) + 1,
        "search_history": [attempt],
    }


def _synthesizer_node(state: GraphState) -> dict:
    """Digest the graph result into facts. Reply generation happens outside
    the graph so it can be streamed."""
    failed = bool(state.get("query_error")) and state.get("retry_count", 0) >= 3
    facts = synthesize_response(
        user_message=state["message"],
        graph_result=state.get("graph_result", []),
        failed=failed,
    )
    return {"sqlite_context": facts}


# ── Conditional routing functions ───────────────────────────────────────────

def _route_after_router(state: GraphState) -> str:
    return "bot_node" if state["intent"] == "journaling" else "cypher_agent_node"


def _route_after_executor(state: GraphState) -> str:
    if state.get("query_error"):
        return "self_correct_node" if state.get("retry_count", 0) < 3 else "synthesizer_node"
    return "evaluator_node"


def _route_after_evaluator(state: GraphState) -> str:
    if state.get("eval_passed", True):
        return "synthesizer_node"
    if state.get("eval_retry_count", 0) < 3:
        return "cypher_agent_node"
    return "synthesizer_node"


# ── Graph assembly ───────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    builder = StateGraph(GraphState)

    builder.add_node("router_node", _router_node)
    builder.add_node("bot_node", _bot_node)
    builder.add_node("cypher_agent_node", _cypher_agent_node)
    builder.add_node("db_executor_node", _db_executor_node)
    builder.add_node("self_correct_node", _self_correct_node)
    builder.add_node("evaluator_node", _evaluator_node)
    builder.add_node("synthesizer_node", _synthesizer_node)

    builder.set_entry_point("router_node")
    builder.add_conditional_edges("router_node", _route_after_router)

    builder.add_edge("bot_node", END)
    builder.add_edge("cypher_agent_node", "db_executor_node")
    builder.add_conditional_edges("db_executor_node", _route_after_executor)
    builder.add_edge("self_correct_node", "db_executor_node")
    builder.add_conditional_edges("evaluator_node", _route_after_evaluator)
    builder.add_edge("synthesizer_node", END)

    return builder.compile()


_graph = _build_graph()


# ── Public entrypoint ────────────────────────────────────────────────────────

def run_graph(conversation_id: int, message_content: str, user_id: UUID) -> dict:
    """Run routing + retrieval WITHOUT generating the reply. Returns the final
    GraphState dict (carries `intent` and, for analytical, `sqlite_context`)."""
    initial_state: GraphState = {
        "message": message_content,
        "conversation_id": conversation_id,
        "user_id": str(user_id),
        "intent": "",
        "sqlite_context": "",
        "cypher_query": "",
        "graph_result": [],
        "query_error": "",
        "retry_count": 0,
        "eval_retry_count": 0,
        "eval_passed": False,
        "eval_hint": "",
        "search_history": [],
        "final_response": "",
    }
    return _graph.invoke(initial_state)


def process_message(conversation_id: int, message_content: str, user_id: UUID) -> str:
    """Non-streaming entrypoint (background-task / tests). Runs the graph then
    generates + persists the reply."""
    state = run_graph(conversation_id, message_content, user_id)
    facts = state.get("sqlite_context") or None
    reply = generate_bot_reply(conversation_id, user_id, graph_synthesis=facts)
    store_assistant_message(conversation_id, reply, user_id)
    return reply
