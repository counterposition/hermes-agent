from agent.run_result_display import (
    agent_result_display_error,
    agent_result_error_metadata,
    agent_result_status,
    agent_result_visible_text,
)


def test_successful_result_uses_final_response():
    result = {"final_response": "Hello", "completed": True}

    assert agent_result_status(result) == "complete"
    assert agent_result_visible_text(result) == "Hello"
    assert agent_result_display_error(result) == ""


def test_failed_empty_result_uses_display_error():
    result = {
        "final_response": None,
        "failed": True,
        "error": "raw SDK exception should not win",
        "display_error": "Model request failed\n\nHTTP 400: invalid model",
    }

    assert agent_result_status(result) == "error"
    assert agent_result_visible_text(result) == "Model request failed\n\nHTTP 400: invalid model"
    assert agent_result_display_error(result) == "Model request failed\n\nHTTP 400: invalid model"


def test_display_error_is_redacted_at_presentation_boundary():
    fake_bearer = "bearer-" + "value"
    fake_query = "query-" + "value"
    result = {
        "final_response": None,
        "failed": True,
        "display_error": (
            f"Model request failed Authorization: Bearer {fake_bearer}\n"
            f"https://example.com/callback?access_token={fake_query}"
        ),
        "error_summary": f"Authorization: Bearer {fake_bearer}",
        "base_url": f"https://example.com/api?access_token={fake_query}",
    }

    visible = agent_result_visible_text(result)
    metadata = agent_result_error_metadata(result)

    assert fake_bearer not in visible
    assert fake_query not in visible
    assert fake_bearer not in str(metadata)
    assert fake_query not in str(metadata)
    assert "Authorization: Bearer" in visible


def test_display_error_redaction_failure_fails_closed(monkeypatch):
    import agent.redact as redact

    fake_bearer = "bearer-" + "value"

    def broken_redactor(*args, **kwargs):
        raise RuntimeError("redactor unavailable")

    monkeypatch.setattr(redact, "redact_sensitive_text", broken_redactor)
    result = {
        "final_response": None,
        "failed": True,
        "display_error": f"Authorization: Bearer {fake_bearer}",
    }

    visible = agent_result_visible_text(result)

    assert fake_bearer not in visible
    assert visible == "[redacted: redaction failed]"


def test_error_metadata_extracts_known_fields_only():
    result = {
        "final_response": None,
        "failed": True,
        "error_kind": "non_retryable_client_error",
        "status_code": 400,
        "provider": "openrouter",
        "model": "gemini-3.1-pro",
        "base_url": "https://openrouter.ai/api/v1",
        "error_summary": "HTTP 400: invalid model",
        "api_key": "must-not-leak",
    }

    assert agent_result_error_metadata(result) == {
        "error_kind": "non_retryable_client_error",
        "status_code": 400,
        "provider": "openrouter",
        "model": "gemini-3.1-pro",
        "base_url": "https://openrouter.ai/api/v1",
        "error_summary": "HTTP 400: invalid model",
    }


def test_failed_empty_result_falls_back_to_error_summary():
    result = {
        "final_response": None,
        "failed": True,
        "error_summary": "HTTP 400: gemini-3.1-pro is not a valid model ID",
        "error": "Error code: 400 - giant SDK dump",
    }

    assert agent_result_status(result) == "error"
    assert "HTTP 400: gemini-3.1-pro" in agent_result_visible_text(result)
    assert "giant SDK dump" not in agent_result_visible_text(result)


def test_failed_empty_result_falls_back_to_raw_error():
    result = {
        "final_response": None,
        "failed": True,
        "error": "provider exploded",
    }

    assert agent_result_status(result) == "error"
    assert agent_result_visible_text(result) == "⚠️ Request failed: provider exploded"


def test_empty_sentinel_gets_friendly_text():
    result = {"final_response": "(empty)", "completed": False}

    assert agent_result_status(result) == "complete"
    assert "no visible response" in agent_result_visible_text(result).lower()


def test_partial_empty_result_is_partial_not_hard_error():
    result = {
        "final_response": None,
        "partial": True,
        "error_summary": "Response truncated due to output length limit",
    }

    assert agent_result_status(result) == "partial"
    assert "Response truncated" in agent_result_visible_text(result)
    assert agent_result_visible_text(result).startswith("⚠️ Request incomplete")


def test_partial_with_assistant_text_keeps_text_and_partial_status():
    result = {
        "final_response": "Here is the partial answer",
        "partial": True,
        "error": "Response remained truncated after continuation attempts",
    }

    assert agent_result_status(result) == "partial"
    assert agent_result_visible_text(result) == "Here is the partial answer"
    assert agent_result_display_error(result).startswith("⚠️ Request incomplete")


def test_empty_string_error_response_is_error_status():
    result = {
        "final_response": "",
        "completed": False,
        "error": "runtime detail",
    }

    assert agent_result_status(result) == "error"
    assert agent_result_visible_text(result).startswith("⚠️ Request failed")


def test_legacy_nonempty_error_response_is_complete_status():
    result = {
        "final_response": "Operational fallback message",
        "completed": False,
        "error": "runtime detail",
    }

    assert agent_result_status(result) == "complete"
    assert agent_result_visible_text(result) == "Operational fallback message"


def test_interrupted_status_wins_over_error():
    result = {"interrupted": True, "error": "cancelled", "final_response": "partial"}

    assert agent_result_status(result) == "interrupted"
