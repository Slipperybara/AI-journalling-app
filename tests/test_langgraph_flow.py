"""Smoke tests for the LangGraph flow routing logic. Mocks LLM + Neo4j calls."""
from unittest.mock import MagicMock, patch


def _make_openai_response(content: str):
    """Build a minimal OpenAI response mock."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_router_classifies_journaling():
    from app.langgraph_flow import _router_node
    with patch("app.langgraph_flow.client") as mock_client:
        mock_client.chat.completions.create.return_value = _make_openai_response("journaling")
        result = _router_node({"message": "Had a great workout today"})
    assert result["intent"] == "journaling"


def test_router_classifies_analytical():
    from app.langgraph_flow import _router_node
    with patch("app.langgraph_flow.client") as mock_client:
        mock_client.chat.completions.create.return_value = _make_openai_response("analytical")
        result = _router_node({"message": "What drives my peak performance days?"})
    assert result["intent"] == "analytical"


def test_router_defaults_to_journaling_on_unexpected_output():
    from app.langgraph_flow import _router_node
    with patch("app.langgraph_flow.client") as mock_client:
        mock_client.chat.completions.create.return_value = _make_openai_response("UNKNOWN")
        result = _router_node({"message": "hey"})
    assert result["intent"] == "journaling"


def test_route_after_executor_success_goes_to_evaluator():
    from app.langgraph_flow import _route_after_executor
    state = {"query_error": "", "retry_count": 0}
    assert _route_after_executor(state) == "evaluator_node"


def test_route_after_executor_error_below_max_goes_to_self_correct():
    from app.langgraph_flow import _route_after_executor
    state = {"query_error": "Syntax error", "retry_count": 1}
    assert _route_after_executor(state) == "self_correct_node"


def test_route_after_executor_error_at_max_goes_to_synthesizer():
    from app.langgraph_flow import _route_after_executor
    state = {"query_error": "Syntax error", "retry_count": 3}
    assert _route_after_executor(state) == "synthesizer_node"


def test_route_after_evaluator_satisfied_goes_to_synthesizer():
    from app.langgraph_flow import _route_after_evaluator
    state = {"eval_passed": True, "eval_retry_count": 1}
    assert _route_after_evaluator(state) == "synthesizer_node"


def test_route_after_evaluator_not_satisfied_retries_cypher():
    from app.langgraph_flow import _route_after_evaluator
    state = {"eval_passed": False, "eval_retry_count": 1}
    assert _route_after_evaluator(state) == "cypher_agent_node"


def test_route_after_evaluator_max_retries_goes_to_synthesizer():
    from app.langgraph_flow import _route_after_evaluator
    # Cap is now 3 broadening attempts: after 3 evaluator runs, fall through.
    state = {"eval_passed": False, "eval_retry_count": 3}
    assert _route_after_evaluator(state) == "synthesizer_node"


def test_evaluator_appends_attempt_to_search_history():
    from unittest.mock import patch
    from app.langgraph_flow import _evaluator_node
    with patch("app.langgraph_flow.evaluate_result", return_value={"satisfied": False, "hint": "broaden range"}):
        update = _evaluator_node({
            "message": "what drove my peak performance days?",
            "cypher_query": "MATCH (d:Day) RETURN d LIMIT 5",
            "graph_result": [{"d": "2026-05-27"}, {"d": "2026-05-29"}],
            "eval_retry_count": 0,
        })
    assert update["eval_passed"] is False
    assert update["eval_hint"] == "broaden range"
    assert update["eval_retry_count"] == 1
    assert isinstance(update["search_history"], list) and len(update["search_history"]) == 1
    attempt = update["search_history"][0]
    assert attempt["query"] == "MATCH (d:Day) RETURN d LIMIT 5"
    assert attempt["result_count"] == 2
    assert attempt["eval_passed"] is False
    assert attempt["eval_hint"] == "broaden range"


def test_synthesizer_signals_failure_when_query_failed():
    """Synthesizer no longer addresses the user directly. On failure it returns
    a short internal signal that the bot wraps into a conversational apology."""
    from app.agents.synthesizer import synthesize_response
    result = synthesize_response(
        user_message="anything",
        graph_result=[],
        failed=True,
    )
    lowered = result.lower()
    assert "fail" in lowered or "no factual" in lowered or "no data" in lowered


def test_db_executor_captures_neo4j_error():
    from app.langgraph_flow import _db_executor_node
    with patch("app.langgraph_flow.graph_connect") as mock_ctx:
        mock_ctx.return_value.__enter__.side_effect = Exception("Connection refused")
        result = _db_executor_node({
            "cypher_query": "MATCH (n) RETURN n",
            "retry_count": 0,
        })
    assert result["query_error"] != ""
    assert result["retry_count"] == 1
