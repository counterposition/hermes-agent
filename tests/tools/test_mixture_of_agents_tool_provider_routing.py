import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import yaml

import hermes_cli.models as models_mod
from hermes_cli.config import DEFAULT_CONFIG, get_config_path
from hermes_cli.model_normalize import normalize_model_for_provider
from hermes_constants import VALID_REASONING_EFFORTS

import tools.mixture_of_agents_tool as moa


VALID_PROVIDER_IDS = [
    "openrouter",
    "nous",
    "openai-codex",
    "google-gemini-cli",
    "copilot",
    "copilot-acp",
    "gemini",
    "huggingface",
    "zai",
    "kimi-coding",
    "kimi-coding-cn",
    "minimax",
    "minimax-cn",
    "kilocode",
    "anthropic",
    "alibaba",
    "qwen-oauth",
    "xiaomi",
    "opencode-zen",
    "opencode-go",
    "ai-gateway",
    "deepseek",
    "arcee",
    "xai",
    "nvidia",
    "ollama-cloud",
    "bedrock",
    "custom",
]


@pytest.fixture(autouse=True)
def _clear_moa_state():
    for name in ("_TEMPERATURE_UNSUPPORTED", "_REASONING_UNSUPPORTED", "_codex_warning_seen"):
        value = getattr(moa, name, None)
        if isinstance(value, dict | set):
            value.clear()


@pytest.fixture(autouse=True)
def _stable_provider_registry(monkeypatch):
    monkeypatch.setattr(
        models_mod,
        "list_available_providers",
        lambda: [{"id": provider_id} for provider_id in VALID_PROVIDER_IDS],
    )


def _write_config(data: dict):
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return config_path


@pytest.fixture
def fake_catalogs(monkeypatch):
    catalogs = {
        "openrouter": [
            "anthropic/claude-opus-4.6",
            "google/gemini-3.1-pro-preview",
            "openai/gpt-5.4-pro",
            "x-ai/grok-4.3",
            "qwen/qwen3.5-plus-02-15",
            "minimax/minimax-m2.5",
        ],
        "anthropic": ["claude-opus-4-6"],
        "openai-codex": ["gpt-5.4"],
        "copilot": ["gpt-5.4", "gpt-4.1", "claude-opus-4.6"],
        "copilot-acp": ["gpt-5.4"],
        "nous": ["minimax/minimax-m2.5"],
        "ai-gateway": ["anthropic/claude-opus-4.6"],
        "custom": [],
    }

    def _provider_model_ids(provider, *, force_refresh=False):
        return list(catalogs.get(provider, []))

    monkeypatch.setattr(models_mod, "provider_model_ids", _provider_model_ids)
    return catalogs


@pytest.mark.parametrize("reasoning_value", [None, "", "   "])
def test_load_moa_config_absent_or_blank_reasoning_uses_defaults(fake_catalogs, reasoning_value):
    config = {
        "moa": {
            "reference_models": [{"model": "anthropic/claude-opus-4.6", "reasoning": reasoning_value}],
            "aggregator_model": {"model": "anthropic/claude-opus-4.6"},
        }
    }
    _write_config(config)

    loaded = moa._load_moa_config()

    assert loaded["enabled"] is True
    assert loaded["reference_models"][0]["provider"] == "openrouter"
    assert loaded["reference_models"][0]["reasoning_config"] is None
    assert loaded["aggregator_model"]["provider"] == "openrouter"
    assert loaded["min_successful_references"] == 1


def test_load_moa_config_defaults_when_block_absent(fake_catalogs):
    _write_config({"model": "anthropic/claude-opus-4.6"})

    loaded = moa._load_moa_config()

    assert [entry["model"] for entry in loaded["reference_models"]] == moa.REFERENCE_MODELS
    assert all(entry["provider"] == "openrouter" for entry in loaded["reference_models"])
    assert loaded["aggregator_model"]["model"] == moa.AGGREGATOR_MODEL
    assert loaded["aggregator_model"]["provider"] == "openrouter"
    assert loaded["reference_temperature"] == moa.REFERENCE_TEMPERATURE
    assert loaded["aggregator_temperature"] == moa.AGGREGATOR_TEMPERATURE
    assert loaded["min_successful_references"] == 2


def test_load_moa_config_accepts_string_shorthand(fake_catalogs):
    _write_config(
        {
            "moa": {
                "reference_models": ["gpt-5.4"],
                "aggregator_model": "anthropic/claude-opus-4.6",
            }
        }
    )

    loaded = moa._load_moa_config()

    assert loaded["reference_models"][0]["model"] == "gpt-5.4"
    assert loaded["reference_models"][0]["provider"] == "openrouter"
    assert loaded["reference_models"][0]["reasoning_config"] is None
    assert loaded["aggregator_model"]["model"] == "anthropic/claude-opus-4.6"
    assert loaded["aggregator_model"]["provider"] == "openrouter"


def test_load_moa_config_rejects_unknown_provider(fake_catalogs):
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "anthropic/claude-opus-4.6", "provider": "totally-made-up"}],
                "aggregator_model": {"model": "anthropic/claude-opus-4.6"},
            }
        }
    )

    with pytest.raises(ValueError, match="unknown provider"):
        moa._load_moa_config()


@pytest.mark.parametrize("provider", ["qwen-oauth", "google-gemini-cli", "bedrock"])
def test_load_moa_config_rejects_providers_not_supported_by_aux_router(fake_catalogs, provider):
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "anthropic/claude-opus-4.6", "provider": provider}],
                "aggregator_model": {"model": "anthropic/claude-opus-4.6"},
            }
        }
    )

    with pytest.raises(ValueError, match="not supported by MoA routing"):
        moa._load_moa_config()


def test_load_moa_config_rejects_invalid_reasoning(fake_catalogs):
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "anthropic/claude-opus-4.6", "reasoning": "extreme"}],
                "aggregator_model": {"model": "anthropic/claude-opus-4.6"},
            }
        }
    )

    with pytest.raises(ValueError) as exc_info:
        moa._load_moa_config()

    message = str(exc_info.value)
    for effort in VALID_REASONING_EFFORTS:
        assert effort in message
    assert "none" in message


@pytest.mark.parametrize(
    ("config", "expected_match"),
    [
        ({"moa": {"reference_models": [], "aggregator_model": {"model": "anthropic/claude-opus-4.6"}}}, "reference_models"),
        ({"moa": {"reference_models": ["anthropic/claude-opus-4.6"], "aggregator_model": None}}, "aggregator_model"),
        ({"moa": {"reference_models": ["anthropic/claude-opus-4.6"], "aggregator_model": {}}}, "aggregator_model"),
        ({"moa": {"reference_models": [{}], "aggregator_model": {"model": "anthropic/claude-opus-4.6"}}}, "reference_models"),
    ],
)
def test_load_moa_config_rejects_invalid_shapes(fake_catalogs, config, expected_match):
    _write_config(config)

    with pytest.raises(ValueError, match=expected_match):
        moa._load_moa_config()


@pytest.mark.parametrize(
    ("roster", "explicit", "expected"),
    [
        (["a", "b", "c"], None, 2),
        (["a"], None, 1),
        (["a", "b", "c"], 1, 1),
    ],
)
def test_min_successful_references_default_and_override(fake_catalogs, roster, explicit, expected):
    config = {
        "moa": {
            "reference_models": roster,
            "aggregator_model": "anthropic/claude-opus-4.6",
        }
    }
    if explicit is not None:
        config["moa"]["min_successful_references"] = explicit
    _write_config(config)

    loaded = moa._load_moa_config()

    assert loaded["min_successful_references"] == expected


@pytest.mark.parametrize("value", [0, 3])
def test_min_successful_references_out_of_range(fake_catalogs, value):
    _write_config(
        {
            "moa": {
                "reference_models": ["anthropic/claude-opus-4.6", "gpt-5.4"],
                "aggregator_model": "anthropic/claude-opus-4.6",
                "min_successful_references": value,
            }
        }
    )

    with pytest.raises(ValueError, match="min_successful_references"):
        moa._load_moa_config()


def test_model_catalog_mismatch_warns_but_does_not_raise(monkeypatch, caplog):
    monkeypatch.setattr(models_mod, "provider_model_ids", lambda provider, *, force_refresh=False: ["gpt-5.4"])
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "not-in-catalog", "provider": "openai-codex"}],
                "aggregator_model": {"model": "gpt-5.4", "provider": "openai-codex"},
            }
        }
    )

    loaded = moa._load_moa_config(emit_warnings=True)

    assert loaded["reference_models"][0]["model"] == "not-in-catalog"
    assert "not in openai-codex catalog" in caplog.text


@pytest.mark.asyncio
async def test_kill_switch_short_circuits_fail_closed(fake_catalogs, monkeypatch):
    _write_config(
        {
            "moa": {
                "enabled": False,
                "reference_models": ["anthropic/claude-opus-4.6"],
                "aggregator_model": "anthropic/claude-opus-4.6",
            }
        }
    )
    monkeypatch.setattr(moa, "_debug", SimpleNamespace(log_call=MagicMock(), save=MagicMock(), active=False))

    result = json.loads(await moa.mixture_of_agents_tool("solve this"))

    assert result == {
        "success": False,
        "response": "",
        "models_used": {"reference_models": [], "aggregator_model": ""},
        "error": "MoA disabled via moa.enabled=false",
    }
    assert moa.check_moa_requirements() is False


def test_check_moa_requirements_is_config_aware(fake_catalogs, monkeypatch):
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "gpt-5.4", "provider": "openai-codex"}],
                "aggregator_model": {"model": "gpt-5.4", "provider": "openai-codex"},
            }
        }
    )
    monkeypatch.setattr(
        moa,
        "_provider_has_credentials",
        lambda provider, model=None: provider == "openai-codex",
    )

    assert moa.check_moa_requirements() is True


def test_check_moa_requirements_reports_missing_provider(fake_catalogs, monkeypatch, caplog):
    _write_config(
        {
            "moa": {
                "reference_models": ["anthropic/claude-opus-4.6"],
                "aggregator_model": "anthropic/claude-opus-4.6",
            }
        }
    )
    monkeypatch.setattr(moa, "_provider_has_credentials", lambda provider, model=None: False)

    assert moa.check_moa_requirements() is False
    assert "openrouter" in caplog.text.lower()


@pytest.mark.parametrize(
    ("provider", "model", "reasoning_config", "expected"),
    [
        (
            "openrouter",
            "qwen/qwen3.5-plus-02-15",
            {"enabled": True, "effort": "xhigh"},
            {"extra_body": {"reasoning": {"enabled": True, "effort": "xhigh"}}},
        ),
        (
            "openrouter",
            "qwen/qwen3.5-plus-02-15",
            {"enabled": False},
            {"extra_body": {"reasoning": {"enabled": False}}},
        ),
        ("openrouter", "minimax/minimax-m2.5", {"enabled": True, "effort": "high"}, {}),
        (
            "nous",
            "minimax/minimax-m2.5",
            {"enabled": True, "effort": "medium"},
            {"extra_body": {"reasoning": {"enabled": True, "effort": "medium"}}},
        ),
        (
            "copilot",
            "gpt-5.4",
            {"enabled": True, "effort": "xhigh"},
            {"extra_body": {"reasoning": {"effort": "high"}}},
        ),
        ("copilot", "gpt-5.4", {"enabled": False}, {}),
        ("copilot", "gpt-4.1", {"enabled": True, "effort": "high"}, {}),
        (
            "ai-gateway",
            "anthropic/claude-opus-4.6",
            {"enabled": False},
            {"extra_body": {"reasoning": {"enabled": False}}},
        ),
        (
            "custom",
            "gpt-5.4",
            {"enabled": True, "effort": "high"},
            {"reasoning_effort": "high"},
        ),
        (
            "openai-codex",
            "gpt-5.4",
            {"enabled": True, "effort": "high"},
            {"reasoning_config": {"enabled": True, "effort": "high"}},
        ),
        (
            "anthropic",
            "claude-opus-4-6",
            {"enabled": True, "effort": "high"},
            {"reasoning_config": {"enabled": True, "effort": "high"}},
        ),
    ],
)
def test_reasoning_kwargs_translation(monkeypatch, provider, model, reasoning_config, expected):
    efforts = {
        "gpt-5.4": ["low", "medium", "high"],
        "gpt-4.1": [],
        "claude-opus-4.6": ["medium", "high"],
    }
    monkeypatch.setattr(
        models_mod,
        "github_model_reasoning_efforts",
        lambda model_id, catalog=None, api_key=None: list(efforts.get(model_id, [])),
    )

    assert moa._reasoning_kwargs(provider, model, reasoning_config) == expected


@pytest.mark.parametrize(
    ("supported_efforts", "requested", "expected_effort"),
    [
        (["low", "medium", "high"], "minimal", "low"),
        (["medium", "high"], "minimal", "medium"),
    ],
)
def test_reasoning_kwargs_copilot_remap_branches(monkeypatch, supported_efforts, requested, expected_effort):
    monkeypatch.setattr(
        models_mod,
        "github_model_reasoning_efforts",
        lambda model_id, catalog=None, api_key=None: list(supported_efforts),
    )

    kwargs = moa._reasoning_kwargs("copilot", "gpt-5.4", {"enabled": True, "effort": requested})

    assert kwargs == {"extra_body": {"reasoning": {"effort": expected_effort}}}


# ---------------------------------------------------------------------------
# Parity with AIAgent._build_api_kwargs for routes where both layers are
# intentionally comparable (OpenRouter / Nous / Copilot / AI-Gateway).
# Anthropic + Codex go through adapter-layer tests below instead because
# AIAgent._build_api_kwargs emits a different shape for those paths.
# AIAgent is imported lazily inside the fixture — the autouse
# _isolate_hermes_home conftest fixture sets HERMES_HOME at test runtime,
# and importing run_agent at module scope would initialize logging against
# the real ~/.hermes before that fixture runs.
# ---------------------------------------------------------------------------
def _make_tool_defs(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


@pytest.fixture
def agent_for_parity():
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("web_search")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch("agent.auxiliary_client.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        agent.client = MagicMock()
        yield agent


def _agent_reasoning_view(agent, provider: str) -> dict:
    kwargs = agent._build_api_kwargs([{"role": "user", "content": "hi"}])
    if provider in {"openrouter", "nous", "copilot", "ai-gateway"}:
        reasoning = kwargs.get("extra_body", {}).get("reasoning")
        return {"extra_body": {"reasoning": reasoning}} if reasoning is not None else {}
    return {}


@pytest.mark.parametrize(
    ("provider", "base_url", "model", "reasoning_config", "supported_efforts"),
    [
        (
            "openrouter",
            "https://openrouter.ai/api/v1",
            "qwen/qwen3.5-plus-02-15",
            {"enabled": True, "effort": "xhigh"},
            [],
        ),
        (
            "openrouter",
            "https://openrouter.ai/api/v1",
            "minimax/minimax-m2.5",
            {"enabled": True, "effort": "high"},
            [],
        ),
        (
            "nous",
            "https://inference-api.nousresearch.com/v1",
            "minimax/minimax-m2.5",
            {"enabled": True, "effort": "medium"},
            [],
        ),
        (
            "copilot",
            "https://api.githubcopilot.com",
            "gpt-5.4",
            {"enabled": True, "effort": "xhigh"},
            ["low", "medium", "high"],
        ),
        (
            "copilot",
            "https://api.githubcopilot.com",
            "gpt-5.4",
            {"enabled": True, "effort": "minimal"},
            ["low", "medium", "high"],
        ),
        (
            "copilot",
            "https://api.githubcopilot.com",
            "gpt-5.4",
            {"enabled": False},
            ["low", "medium", "high"],
        ),
        (
            "copilot",
            "https://api.githubcopilot.com",
            "gpt-4.1",
            {"enabled": True, "effort": "high"},
            [],
        ),
        (
            "ai-gateway",
            "https://ai-gateway.vercel.sh/v1",
            "anthropic/claude-opus-4.6",
            {"enabled": False},
            [],
        ),
    ],
)
def test_reasoning_translation_matches_main_agent(
    monkeypatch,
    agent_for_parity,
    provider,
    base_url,
    model,
    reasoning_config,
    supported_efforts,
):
    monkeypatch.setattr(
        models_mod,
        "github_model_reasoning_efforts",
        lambda model_id, catalog=None, api_key=None: list(supported_efforts),
    )

    agent_for_parity.base_url = base_url
    agent_for_parity._base_url_lower = base_url.lower()
    agent_for_parity.provider = provider
    agent_for_parity.model = model
    agent_for_parity.reasoning_config = reasoning_config

    assert moa._reasoning_kwargs(provider, model, reasoning_config) == _agent_reasoning_view(
        agent_for_parity, provider
    )


@pytest.mark.asyncio
async def test_reasoning_unsupported_fallback_is_sticky(fake_catalogs, monkeypatch):
    if not hasattr(moa, "_REASONING_UNSUPPORTED"):
        pytest.skip("sticky fallback not implemented yet")

    calls = []

    class FakeError(Exception):
        def __init__(self):
            super().__init__("reasoning is not supported for this model")
            self.body = {"error": {"code": "unsupported_parameter", "param": "reasoning"}}

    async def _create(**kwargs):
        calls.append(kwargs)
        if "reasoning_effort" in kwargs:
            raise FakeError()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr(moa, "resolve_provider_client", lambda *a, **kw: (fake_client, "gpt-5.4"))
    monkeypatch.setattr(moa, "extract_content_or_reasoning", lambda response: response.choices[0].message.content)

    model_name, content, success = await moa._run_reference_model_safe(
        {"model": "gpt-5.4", "provider": "custom", "reasoning_config": {"enabled": True, "effort": "high"}},
        "hello",
        max_retries=2,
    )

    assert (model_name, content, success) == ("gpt-5.4", "ok", True)
    assert calls[0]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in calls[1]
    assert moa._REASONING_UNSUPPORTED[("custom", "gpt-5.4")] is True

    calls.clear()
    model_name, content, success = await moa._run_reference_model_safe(
        {"model": "gpt-5.4", "provider": "custom", "reasoning_config": {"enabled": True, "effort": "high"}},
        "hello again",
        max_retries=2,
    )

    assert (model_name, content, success) == ("gpt-5.4", "ok", True)
    assert len(calls) == 1
    assert "reasoning_effort" not in calls[0]


@pytest.mark.asyncio
async def test_temperature_unsupported_fallback_is_sticky(fake_catalogs, monkeypatch):
    calls = []

    class FakeError(Exception):
        def __init__(self):
            super().__init__("temperature is not supported for this model")
            self.body = {"error": {"code": "unsupported_parameter", "param": "temperature"}}

    async def _create(**kwargs):
        calls.append(kwargs)
        if "temperature" in kwargs:
            raise FakeError()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr(moa, "resolve_provider_client", lambda *a, **kw: (fake_client, "gpt-5.4"))
    monkeypatch.setattr(moa, "extract_content_or_reasoning", lambda response: response.choices[0].message.content)

    model_name, content, success = await moa._run_reference_model_safe(
        {"model": "gpt-5.4", "provider": "custom", "reasoning_config": None},
        "hello",
        max_retries=2,
    )

    assert (model_name, content, success) == ("gpt-5.4", "ok", True)
    assert calls[0]["temperature"] == moa.REFERENCE_TEMPERATURE
    assert "temperature" not in calls[1]
    assert moa._TEMPERATURE_UNSUPPORTED[("custom", "gpt-5.4")] is True

    calls.clear()
    model_name, content, success = await moa._run_reference_model_safe(
        {"model": "gpt-5.4", "provider": "custom", "reasoning_config": None},
        "hello again",
        max_retries=2,
    )

    assert (model_name, content, success) == ("gpt-5.4", "ok", True)
    assert len(calls) == 1
    assert "temperature" not in calls[0]


@pytest.mark.asyncio
async def test_reference_model_init_failure_is_reported_as_model_failure(fake_catalogs, monkeypatch):
    """A raise from resolve_provider_client must be reported as a per-model
    soft failure — not propagate up and crash the whole MoA call."""
    def _boom(*args, **kwargs):
        raise RuntimeError("bad provider state")

    monkeypatch.setattr(moa, "resolve_provider_client", _boom)

    model_name, content, success = await moa._run_reference_model_safe(
        {"model": "gpt-5.4", "provider": "openai-codex", "reasoning_config": None},
        "hello",
        max_retries=1,
    )

    assert model_name == "gpt-5.4"
    assert success is False
    assert "could not be initialized" in content


@pytest.mark.asyncio
async def test_run_reference_model_safe_supports_copilot_acp(fake_catalogs):
    from agent.copilot_acp_client import CopilotACPClient

    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="ok",
                    reasoning=None,
                    reasoning_content=None,
                    reasoning_details=None,
                )
            )
        ]
    )

    with patch("agent.auxiliary_client._read_main_model", return_value="gpt-5.4"), \
         patch.object(CopilotACPClient, "_create_chat_completion", return_value=fake_response) as mock_create, \
         patch("hermes_cli.auth.resolve_external_process_provider_credentials", return_value={
             "provider": "copilot-acp",
             "api_key": "copilot-acp",
             "base_url": "acp://copilot",
             "command": "/usr/bin/copilot",
             "args": ["--acp", "--stdio"],
         }):
        model_name, content, success = await moa._run_reference_model_safe(
            {"model": "gpt-5.4", "provider": "copilot-acp", "reasoning_config": None},
            "hello",
            max_retries=1,
        )

    assert (model_name, content, success) == ("gpt-5.4", "ok", True)
    mock_create.assert_called_once()


def test_default_config_moa_entries_exist_in_provider_catalogs(fake_catalogs):
    default_moa = DEFAULT_CONFIG["moa"]
    entries = list(default_moa["reference_models"]) + [default_moa["aggregator_model"]]

    for entry in entries:
        normalized_model = normalize_model_for_provider(entry["model"], entry["provider"])
        assert normalized_model in fake_catalogs[entry["provider"]]


def test_module_constant_fallbacks_exist_in_openrouter_catalog(fake_catalogs):
    for model in moa.REFERENCE_MODELS + [moa.AGGREGATOR_MODEL]:
        normalized_model = normalize_model_for_provider(model, "openrouter")
        assert normalized_model in fake_catalogs["openrouter"]


def test_debug_parameters_capture_provider_and_reasoning(fake_catalogs):
    _write_config(
        {
            "moa": {
                "reference_models": [
                    {"model": "anthropic/claude-opus-4.6", "provider": "openrouter", "reasoning": "high"},
                    {"model": "gpt-5.4", "provider": "openai-codex"},
                ],
                "aggregator_model": {"model": "anthropic/claude-opus-4.6", "provider": "ai-gateway", "reasoning": "none"},
            }
        }
    )

    loaded = moa._load_moa_config()

    ref0 = loaded["reference_models"][0]
    ref1 = loaded["reference_models"][1]
    agg = loaded["aggregator_model"]

    assert ref0["provider"] == "openrouter"
    assert ref0["reasoning_config"] == {"enabled": True, "effort": "high"}
    assert ref1["provider"] == "openai-codex"
    assert ref1["reasoning_config"] is None
    assert agg["provider"] == "ai-gateway"
    assert agg["reasoning_config"] == {"enabled": False}


# ---------------------------------------------------------------------------
# Adversarial-review regression tests
# (See codex adversarial review of commit 41c8a4b4 — these guard the four
#  fixes against future drift.)
# ---------------------------------------------------------------------------
def test_codex_adapter_clamps_minimal_to_low(monkeypatch):
    """Responses API rejects 'minimal' on GPT-5.x — clamp before send."""
    from agent.auxiliary_client import _CodexCompletionsAdapter

    captured: dict = {}

    class FakeStream:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter(())

        def get_final_response(self):
            return SimpleNamespace(output=[], usage=None, model="gpt-5.4")

    fake_client = SimpleNamespace(
        responses=SimpleNamespace(stream=lambda **kwargs: FakeStream(**kwargs))
    )

    adapter = _CodexCompletionsAdapter(fake_client, "gpt-5.4")
    adapter.create(
        model="gpt-5.4",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_config={"enabled": True, "effort": "minimal"},
    )

    assert captured["reasoning"]["effort"] == "low"


def test_anthropic_adapter_does_not_clobber_thinking_temperature(monkeypatch):
    """build_anthropic_kwargs forces temperature=1 when thinking is enabled.
    The adapter must not override that with the caller's temperature."""
    from agent import auxiliary_client as aux

    captured: dict = {}

    fake_client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: (captured.update(kw), SimpleNamespace(
            content=[],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        ))[1])
    )

    # Stub the anthropic adapter's helpers so we don't pull in the real SDK.
    def fake_build_kwargs(**kwargs):
        # Simulate the thinking-models contract: temperature is forced to 1.
        return {
            "model": kwargs["model"],
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": kwargs["max_tokens"],
            "temperature": 1,
            "thinking": {"type": "enabled", "budget_tokens": 1024},
        }

    class FakeTransport:
        def normalize_response(self, _response, strip_tool_prefix=False):
            return SimpleNamespace(
                content="",
                tool_calls=None,
                reasoning=None,
                finish_reason="stop",
            )

    monkeypatch.setattr("agent.anthropic_adapter.build_anthropic_kwargs", fake_build_kwargs)
    monkeypatch.setattr("agent.transports.get_transport", lambda _name: FakeTransport())

    adapter = aux._AnthropicCompletionsAdapter(fake_client, "claude-opus-4-6")
    adapter.create(
        model="claude-opus-4-6",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.4,  # caller supplied — must NOT clobber thinking's 1
        reasoning_config={"enabled": True, "effort": "high"},
    )

    assert captured["temperature"] == 1


def test_codex_adapter_receives_reasoning_config_from_moa_shape():
    """MoA → Codex adapter handoff: the kwarg name is `reasoning_config`,
    and the adapter must translate it into the Responses API payload shape
    (reasoning={"effort","summary"} + include=["reasoning.encrypted_content"]).
    Guards the MoA/adapter boundary that AIAgent._build_api_kwargs can't
    cover — its Codex branch emits the Responses shape directly, not the
    adapter-facing `reasoning_config` kwarg.
    """
    from agent.auxiliary_client import _CodexCompletionsAdapter

    moa_kwargs = moa._reasoning_kwargs(
        "openai-codex", "gpt-5.4", {"enabled": True, "effort": "high"}
    )
    assert moa_kwargs == {"reasoning_config": {"enabled": True, "effort": "high"}}

    captured: dict = {}

    class FakeStream:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter(())

        def get_final_response(self):
            return SimpleNamespace(output=[], usage=None, model="gpt-5.4")

    fake_client = SimpleNamespace(
        responses=SimpleNamespace(stream=lambda **kwargs: FakeStream(**kwargs))
    )

    adapter = _CodexCompletionsAdapter(fake_client, "gpt-5.4")
    adapter.create(
        model="gpt-5.4",
        messages=[{"role": "user", "content": "hi"}],
        **moa_kwargs,
    )

    assert captured["reasoning"] == {"effort": "high", "summary": "auto"}
    assert captured["include"] == ["reasoning.encrypted_content"]


def test_anthropic_adapter_receives_reasoning_config_from_moa_shape(monkeypatch):
    """MoA → Anthropic adapter handoff: the adapter must forward the
    `reasoning_config` kwarg to build_anthropic_kwargs unchanged (not as
    None). Guards against the exact regression where the adapter hard-codes
    reasoning_config=None and silently drops per-model reasoning for
    Anthropic-routed MoA slots.
    """
    from agent import auxiliary_client as aux

    moa_kwargs = moa._reasoning_kwargs(
        "anthropic", "claude-opus-4-6", {"enabled": True, "effort": "high"}
    )
    assert moa_kwargs == {"reasoning_config": {"enabled": True, "effort": "high"}}

    captured_build_kwargs: dict = {}

    def fake_build_kwargs(**kwargs):
        captured_build_kwargs.update(kwargs)
        return {
            "model": kwargs["model"],
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": kwargs["max_tokens"],
        }

    fake_client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(
            content=[],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        ))
    )

    class FakeTransport:
        def normalize_response(self, _response, strip_tool_prefix=False):
            return SimpleNamespace(
                content="",
                tool_calls=None,
                reasoning=None,
                finish_reason="stop",
            )

    monkeypatch.setattr("agent.anthropic_adapter.build_anthropic_kwargs", fake_build_kwargs)
    monkeypatch.setattr("agent.transports.get_transport", lambda _name: FakeTransport())

    adapter = aux._AnthropicCompletionsAdapter(fake_client, "claude-opus-4-6")
    adapter.create(
        model="claude-opus-4-6",
        messages=[{"role": "user", "content": "hi"}],
        **moa_kwargs,
    )

    assert captured_build_kwargs["reasoning_config"] == {"enabled": True, "effort": "high"}


def test_named_custom_provider_accepted(fake_catalogs):
    """`custom:<name>` slugs should pass MoA provider validation, not raise."""
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "my-model", "provider": "custom:internal"}],
                "aggregator_model": {"model": "my-model", "provider": "custom:internal"},
            }
        }
    )

    loaded = moa._load_moa_config()

    assert loaded["reference_models"][0]["provider"] == "custom:internal"
    assert loaded["aggregator_model"]["provider"] == "custom:internal"


def test_copilot_preflight_uses_auth_status(monkeypatch):
    """The Copilot preflight must consult get_auth_status() (which honors the
    `gh auth token` fallback) rather than just env-var presence."""
    import hermes_cli.auth as auth_mod

    monkeypatch.delenv("COPILOT_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        auth_mod,
        "get_auth_status",
        lambda provider: {"logged_in": True} if provider == "copilot" else {},
    )

    assert moa._provider_has_credentials("copilot") is True


def test_openrouter_preflight_uses_runtime_resolution(monkeypatch):
    """OpenRouter preflight must honor pooled/runtime credentials, not just env vars."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fake_client = MagicMock()

    with patch.object(moa, "resolve_provider_client", return_value=(fake_client, "google/gemini-3-flash-preview")) as mock_resolve:
        assert moa._provider_has_credentials("openrouter") is True

    mock_resolve.assert_called_once_with("openrouter", model=None)
    fake_client.close.assert_called_once()


def test_custom_preflight_uses_runtime_resolution_fallback(monkeypatch):
    """Custom preflight must honor the runtime custom fallback chain."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    fake_client = MagicMock()

    with patch.object(moa, "resolve_provider_client", return_value=(fake_client, "gpt-5.2-codex")) as mock_resolve:
        assert moa._provider_has_credentials("custom") is True

    mock_resolve.assert_called_once_with("custom", model=None)
    fake_client.close.assert_called_once()


def test_custom_named_provider_preflight_passes(monkeypatch):
    """Named custom providers (`custom:<name>`) bypass per-provider env probes
    and are treated as available — runtime resolution does the real check."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("CUSTOM_INTERNAL_API_KEY", raising=False)

    assert moa._provider_has_credentials("custom:internal") is True


def test_anthropic_preflight_uses_runtime_resolution(monkeypatch):
    """Anthropic preflight should follow the same resolver path as runtime."""
    fake_client = MagicMock()
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "claude-opus-4-6", "provider": "anthropic"}],
                "aggregator_model": {"model": "claude-opus-4-6", "provider": "anthropic"},
            }
        }
    )

    with patch.object(
        moa,
        "resolve_provider_client",
        return_value=(fake_client, "claude-opus-4-6"),
    ) as mock_resolve:
        assert moa.get_moa_preflight_status() == (True, None)

    assert mock_resolve.call_args_list == [
        call("anthropic", model="claude-opus-4-6"),
        call("anthropic", model="claude-opus-4-6"),
    ]
    assert fake_client.close.call_count == 2


@pytest.mark.asyncio
async def test_per_call_reference_override_recomputes_adaptive_quorum(fake_catalogs, monkeypatch):
    _write_config(
        {
            "moa": {
                "reference_models": [
                    "anthropic/claude-opus-4.6",
                    "openai/gpt-5.4-pro",
                ],
                "aggregator_model": "anthropic/claude-opus-4.6",
            }
        }
    )
    monkeypatch.setattr(
        moa,
        "_debug",
        SimpleNamespace(log_call=MagicMock(), save=MagicMock(), active=False),
    )
    monkeypatch.setattr(
        moa,
        "_run_reference_model_safe",
        AsyncMock(return_value=("anthropic/claude-opus-4.6", "reference ok", True)),
    )
    monkeypatch.setattr(
        moa,
        "_run_aggregator_model",
        AsyncMock(return_value="aggregated"),
    )

    result = json.loads(
        await moa.mixture_of_agents_tool(
            "solve this",
            reference_models=["anthropic/claude-opus-4.6"],
        )
    )

    assert result["success"] is True
    assert result["response"] == "aggregated"
    assert result["models_used"]["reference_models"] == ["anthropic/claude-opus-4.6"]
