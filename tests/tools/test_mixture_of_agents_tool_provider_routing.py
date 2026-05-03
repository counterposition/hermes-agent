import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    "lmstudio",
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
    "tencent-tokenhub",
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
    moa._codex_warning_seen.clear()
    moa._reasoning_skip_warning_seen.clear()


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
            "anthropic/claude-opus-4.7",
            "google/gemini-3.1-pro-preview",
            "openai/gpt-5.5-pro",
            "qwen/qwen3.6-plus",
            "qwen/qwen3.5-plus-02-15",
        ],
        "anthropic": ["claude-opus-4-6"],
        "openai-codex": ["gpt-5.4"],
        "copilot": ["gpt-5.4", "gpt-4.1"],
        "copilot-acp": ["gpt-5.4"],
        "nous": ["minimax/minimax-m2.5"],
        "ai-gateway": ["anthropic/claude-opus-4.7"],
        "lmstudio": ["local-reasoner"],
        "tencent-tokenhub": ["hy3-preview"],
        "custom": [],
    }

    def _provider_model_ids(provider, *, force_refresh=False):
        return list(catalogs.get(provider, []))

    monkeypatch.setattr(models_mod, "provider_model_ids", _provider_model_ids)
    return catalogs


@pytest.mark.parametrize("reasoning_value", [None, "", "   "])
def test_load_moa_config_absent_or_blank_reasoning_uses_defaults(fake_catalogs, reasoning_value):
    _write_config(
        {
            "moa": {
                "reference_models": [
                    {
                        "model": "anthropic/claude-opus-4.7",
                        "reasoning": reasoning_value,
                    }
                ],
                "aggregator_model": {"model": "anthropic/claude-opus-4.7"},
            }
        }
    )

    loaded = moa._load_moa_config()

    assert loaded["enabled"] is True
    assert loaded["reference_models"][0]["provider"] == "openrouter"
    assert loaded["reference_models"][0]["reasoning_config"] is None
    assert loaded["aggregator_model"]["provider"] == "openrouter"
    assert loaded["aggregator_model"]["reasoning_config"] is None
    assert loaded["min_successful_references"] == 1


def test_load_moa_config_defaults_when_block_absent(fake_catalogs):
    _write_config({"model": "anthropic/claude-opus-4.7"})

    loaded = moa._load_moa_config()

    assert [entry["model"] for entry in loaded["reference_models"]] == moa.REFERENCE_MODELS
    assert all(entry["provider"] == "openrouter" for entry in loaded["reference_models"])
    assert all(entry["reasoning_config"] == {"enabled": True, "effort": "xhigh"} for entry in loaded["reference_models"])
    assert loaded["aggregator_model"]["model"] == moa.AGGREGATOR_MODEL
    assert loaded["aggregator_model"]["provider"] == "openrouter"
    assert loaded["aggregator_model"]["reasoning_config"] == {"enabled": True, "effort": "xhigh"}
    assert loaded["reference_temperature"] == moa.REFERENCE_TEMPERATURE
    assert loaded["aggregator_temperature"] == moa.AGGREGATOR_TEMPERATURE
    assert loaded["min_successful_references"] == 2


def test_load_moa_config_accepts_explicit_enabled_true(fake_catalogs):
    _write_config(
        {
            "moa": {
                "enabled": True,
                "reference_models": ["anthropic/claude-opus-4.7"],
                "aggregator_model": "anthropic/claude-opus-4.7",
            }
        }
    )

    loaded = moa._load_moa_config()

    assert loaded["enabled"] is True


def test_load_moa_config_rejects_non_bool_enabled(fake_catalogs):
    _write_config(
        {
            "moa": {
                "enabled": "yes",
                "reference_models": ["anthropic/claude-opus-4.7"],
                "aggregator_model": "anthropic/claude-opus-4.7",
            }
        }
    )

    with pytest.raises(ValueError, match="moa.enabled"):
        moa._load_moa_config()


def test_load_moa_config_accepts_string_shorthand(fake_catalogs):
    _write_config(
        {
            "moa": {
                "reference_models": ["gpt-5.4"],
                "aggregator_model": "anthropic/claude-opus-4.7",
            }
        }
    )

    loaded = moa._load_moa_config()

    assert loaded["reference_models"][0]["model"] == "gpt-5.4"
    assert loaded["reference_models"][0]["provider"] == "openrouter"
    assert loaded["reference_models"][0]["reasoning_config"] is None
    assert loaded["aggregator_model"]["model"] == "anthropic/claude-opus-4.7"
    assert loaded["aggregator_model"]["provider"] == "openrouter"


def test_load_moa_config_preserves_dict_entries(fake_catalogs):
    _write_config(
        {
            "moa": {
                "reference_models": [
                    {
                        "model": "gpt-5.4",
                        "provider": "openai-codex",
                        "reasoning": "high",
                    }
                ],
                "aggregator_model": {
                    "model": "claude-opus-4-6",
                    "provider": "anthropic",
                    "reasoning": "none",
                },
            }
        }
    )

    loaded = moa._load_moa_config()

    assert loaded["reference_models"][0] == {
        "model": "gpt-5.4",
        "provider": "openai-codex",
        "reasoning_config": {"enabled": True, "effort": "high"},
    }
    assert loaded["aggregator_model"] == {
        "model": "claude-opus-4-6",
        "provider": "anthropic",
        "reasoning_config": {"enabled": False},
    }


def test_load_moa_config_normalizes_provider_aliases(fake_catalogs):
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "hy3-preview", "provider": "tokenhub"}],
                "aggregator_model": {"model": "local-reasoner", "provider": "lm-studio"},
            }
        }
    )

    loaded = moa._load_moa_config()

    assert loaded["reference_models"][0]["provider"] == "tencent-tokenhub"
    assert loaded["aggregator_model"]["provider"] == "lmstudio"


def test_load_moa_config_accepts_named_custom_provider_slug(fake_catalogs):
    _write_config(
        {
            "custom_providers": [
                {
                    "name": "internal",
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test",
                }
            ],
            "moa": {
                "reference_models": [{"model": "my-model", "provider": "custom:internal"}],
                "aggregator_model": {"model": "my-model", "provider": "custom:internal"},
            },
        }
    )

    loaded = moa._load_moa_config()

    assert loaded["reference_models"][0]["provider"] == "custom:internal"
    assert loaded["aggregator_model"]["provider"] == "custom:internal"


def test_load_moa_config_rejects_unknown_provider(fake_catalogs):
    _write_config(
        {
            "moa": {
                "reference_models": [
                    {"model": "anthropic/claude-opus-4.7", "provider": "totally-made-up"}
                ],
                "aggregator_model": {"model": "anthropic/claude-opus-4.7"},
            }
        }
    )

    with pytest.raises(ValueError, match="unknown provider"):
        moa._load_moa_config()


@pytest.mark.parametrize("reasoning", ["extreme", "max"])
def test_load_moa_config_rejects_invalid_reasoning(fake_catalogs, reasoning):
    _write_config(
        {
            "moa": {
                "reference_models": [
                    {"model": "anthropic/claude-opus-4.7", "reasoning": reasoning}
                ],
                "aggregator_model": {"model": "anthropic/claude-opus-4.7"},
            }
        }
    )

    with pytest.raises(ValueError) as exc_info:
        moa._load_moa_config()

    message = str(exc_info.value)
    for effort in (*VALID_REASONING_EFFORTS, "none"):
        assert effort in message
    assert "max" not in message.replace(repr(reasoning), "")


@pytest.mark.parametrize("reasoning", VALID_REASONING_EFFORTS)
def test_load_moa_config_accepts_every_valid_reasoning_effort(fake_catalogs, reasoning):
    _write_config(
        {
            "moa": {
                "reference_models": [
                    {
                        "model": "anthropic/claude-opus-4.7",
                        "reasoning": reasoning,
                    }
                ],
                "aggregator_model": {
                    "model": "anthropic/claude-opus-4.7",
                    "reasoning": "none",
                },
            }
        }
    )

    loaded = moa._load_moa_config()

    assert loaded["reference_models"][0]["reasoning_config"] == {
        "enabled": True,
        "effort": reasoning,
    }
    assert loaded["aggregator_model"]["reasoning_config"] == {"enabled": False}


@pytest.mark.parametrize(
    ("config", "expected_match"),
    [
        (
            {
                "moa": {
                    "reference_models": [],
                    "aggregator_model": {"model": "anthropic/claude-opus-4.7"},
                }
            },
            "reference_models",
        ),
        (
            {
                "moa": {
                    "reference_models": ["anthropic/claude-opus-4.7"],
                    "aggregator_model": None,
                }
            },
            "aggregator_model",
        ),
        (
            {
                "moa": {
                    "reference_models": ["anthropic/claude-opus-4.7"],
                    "aggregator_model": {},
                }
            },
            "aggregator_model",
        ),
        (
            {
                "moa": {
                    "reference_models": [{}],
                    "aggregator_model": {"model": "anthropic/claude-opus-4.7"},
                }
            },
            "reference_models",
        ),
    ],
)
def test_load_moa_config_rejects_invalid_shapes(fake_catalogs, config, expected_match):
    _write_config(config)

    with pytest.raises(ValueError, match=expected_match):
        moa._load_moa_config()


def test_load_moa_config_surfaces_raw_moa_read_failure(fake_catalogs, monkeypatch):
    _write_config({"model": "anthropic/claude-opus-4.7"})
    monkeypatch.setattr(
        moa,
        "_read_raw_moa_subtree",
        lambda: (_ for _ in ()).throw(
            ValueError("unable to read raw moa config for validation: boom")
        ),
    )

    with pytest.raises(ValueError, match="unable to read raw moa config"):
        moa._load_moa_config()


def test_normalize_entry_surfaces_provider_normalization_errors(monkeypatch):
    def _boom(provider):
        raise RuntimeError(f"bad provider alias: {provider}")

    monkeypatch.setattr(models_mod, "normalize_provider", _boom)

    with pytest.raises(RuntimeError, match="bad provider alias"):
        moa._normalize_entry(
            {"model": "m", "provider": "lm-studio"},
            idx=0,
            is_aggregator=False,
        )


def test_load_moa_config_surfaces_custom_provider_helper_errors(
    fake_catalogs,
    monkeypatch,
):
    import hermes_cli.config as config_mod

    _write_config(
        {
            "moa": {
                "reference_models": ["anthropic/claude-opus-4.7"],
                "aggregator_model": "anthropic/claude-opus-4.7",
            }
        }
    )

    def _boom(config):
        raise RuntimeError("bad custom provider config")

    monkeypatch.setattr(config_mod, "get_compatible_custom_providers", _boom)

    with pytest.raises(RuntimeError, match="bad custom provider config"):
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
            "aggregator_model": "anthropic/claude-opus-4.7",
        }
    }
    if explicit is not None:
        config["moa"]["min_successful_references"] = explicit
    _write_config(config)

    loaded = moa._load_moa_config()

    assert loaded["min_successful_references"] == expected


@pytest.mark.parametrize("value", [0, 3, 1.5, True])
def test_min_successful_references_out_of_range(fake_catalogs, value):
    _write_config(
        {
            "moa": {
                "reference_models": ["anthropic/claude-opus-4.7", "gpt-5.4"],
                "aggregator_model": "anthropic/claude-opus-4.7",
                "min_successful_references": value,
            }
        }
    )

    with pytest.raises(ValueError, match="min_successful_references"):
        moa._load_moa_config()


@pytest.mark.asyncio
async def test_per_call_reference_override_honors_explicit_quorum(fake_catalogs, monkeypatch):
    _write_config(
        {
            "moa": {
                "reference_models": ["anthropic/claude-opus-4.7", "openai/gpt-5.5-pro"],
                "aggregator_model": "anthropic/claude-opus-4.7",
                "min_successful_references": 2,
            }
        }
    )
    monkeypatch.setattr(
        moa,
        "_debug",
        SimpleNamespace(log_call=MagicMock(), save=MagicMock(), active=False),
    )

    result = json.loads(
        await moa.mixture_of_agents_tool("solve this", reference_models=["solo"])
    )

    assert result["success"] is False
    assert "pinned to 2" in result["error"]


def test_model_catalog_mismatch_warns_but_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        models_mod,
        "provider_model_ids",
        lambda provider, *, force_refresh=False: ["gpt-5.4"],
    )
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "not-in-catalog", "provider": "openai-codex"}],
                "aggregator_model": {"model": "gpt-5.4", "provider": "openai-codex"},
            }
        }
    )

    with patch.object(moa.logger, "warning") as warn:
        loaded = moa._load_moa_config(emit_warnings=True)

    assert loaded["reference_models"][0]["model"] == "not-in-catalog"
    warn.assert_any_call(
        "MoA: model %r not in %s catalog — may fail at call time",
        "not-in-catalog",
        "openai-codex",
    )


@pytest.mark.asyncio
async def test_kill_switch_short_circuits_fail_closed(fake_catalogs, monkeypatch):
    _write_config(
        {
            "moa": {
                "enabled": False,
                "reference_models": ["anthropic/claude-opus-4.7"],
                "aggregator_model": "anthropic/claude-opus-4.7",
            }
        }
    )
    monkeypatch.setattr(
        moa,
        "_debug",
        SimpleNamespace(log_call=MagicMock(), save=MagicMock(), active=False),
    )

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
                "reference_models": ["anthropic/claude-opus-4.7"],
                "aggregator_model": "anthropic/claude-opus-4.7",
            }
        }
    )
    monkeypatch.setattr(moa, "_provider_has_credentials", lambda provider, model=None: False)

    available, hint = moa.get_moa_preflight_status()

    assert available is False
    assert hint == "credentials for openrouter"
    assert "openrouter" in caplog.text.lower()


def test_custom_provider_slug_does_not_bypass_preflight(fake_catalogs, monkeypatch):
    _write_config(
        {
            "custom_providers": [
                {
                    "name": "internal",
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test",
                }
            ],
            "moa": {
                "reference_models": [{"model": "my-model", "provider": "custom:internal"}],
                "aggregator_model": {"model": "my-model", "provider": "custom:internal"},
            },
        }
    )
    monkeypatch.setattr(moa, "resolve_provider_client", lambda *args, **kwargs: (None, "my-model"))

    available, hint = moa.get_moa_preflight_status()

    assert available is False
    assert hint == "credentials for custom:internal"


def test_preflight_uses_resolver_and_closes_clients(fake_catalogs, monkeypatch):
    _write_config(
        {
            "moa": {
                "reference_models": [{"model": "gpt-5.4", "provider": "openai-codex"}],
                "aggregator_model": {"model": "claude-opus-4-6", "provider": "anthropic"},
            }
        }
    )
    client = SimpleNamespace(close=MagicMock())
    calls = []

    def _resolve(provider, model=None, async_mode=False):
        calls.append((provider, model, async_mode))
        return client, model

    monkeypatch.setattr(moa, "resolve_provider_client", _resolve)

    available, hint = moa.get_moa_preflight_status()

    assert available is True
    assert hint is None
    assert calls == [
        ("openai-codex", "gpt-5.4", False),
        ("anthropic", "claude-opus-4-6", False),
    ]
    assert client.close.call_count == 2


@pytest.mark.asyncio
async def test_create_chat_completion_supports_sync_create():
    calls = []

    def _create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))

    response = await moa._create_chat_completion(client, model="m", messages=[])

    assert response.choices[0].message.content == "ok"
    assert calls == [{"model": "m", "messages": []}]


def test_build_api_params_omits_extra_body_without_reasoning():
    params = moa._build_api_params(
        "openrouter",
        "anthropic/claude-opus-4.7",
        [{"role": "user", "content": "hi"}],
        None,
        0.6,
    )

    assert "extra_body" not in params


def test_build_api_params_forwards_reasoning_for_pr1_supported_providers():
    reasoning_config = {"enabled": True, "effort": "high"}

    for provider in ("openrouter", "nous", "ai-gateway", "openai-codex"):
        params = moa._build_api_params(
            provider,
            "model",
            [{"role": "user", "content": "hi"}],
            reasoning_config,
            0.6,
        )
        assert params["extra_body"] == {"reasoning": reasoning_config}


def test_build_api_params_skips_reasoning_for_unsupported_provider_once():
    reasoning_config = {"enabled": True, "effort": "high"}

    with patch.object(moa.logger, "warning") as warn:
        first = moa._build_api_params(
            "anthropic",
            "claude-opus-4-6",
            [{"role": "user", "content": "hi"}],
            reasoning_config,
            0.6,
        )
        second = moa._build_api_params(
            "anthropic",
            "claude-opus-4-6",
            [{"role": "user", "content": "again"}],
            reasoning_config,
            0.6,
        )

    assert "extra_body" not in first
    assert "extra_body" not in second
    warn.assert_called_once()
    assert "not forwarded in PR1" in warn.call_args.args[0]


@pytest.mark.asyncio
async def test_reference_model_forwards_default_max_tokens_and_reasoning(monkeypatch):
    calls = []

    async def _create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )
    monkeypatch.setattr(
        moa,
        "resolve_provider_client",
        lambda *args, **kwargs: (fake_client, "anthropic/claude-opus-4.7"),
    )
    monkeypatch.setattr(
        moa,
        "extract_content_or_reasoning",
        lambda response: response.choices[0].message.content,
    )

    model_name, content, success = await moa._run_reference_model_safe(
        {
            "model": "anthropic/claude-opus-4.7",
            "provider": "openrouter",
            "reasoning_config": {"enabled": True, "effort": "xhigh"},
        },
        "hello",
        max_retries=1,
    )

    assert (model_name, content, success) == (
        "anthropic/claude-opus-4.7",
        "ok",
        True,
    )
    assert calls[0]["max_tokens"] == 32000
    assert calls[0]["extra_body"] == {
        "reasoning": {"enabled": True, "effort": "xhigh"}
    }
    assert calls[0]["temperature"] == moa.REFERENCE_TEMPERATURE


@pytest.mark.asyncio
async def test_reference_model_init_failure_is_reported_as_model_failure(fake_catalogs, monkeypatch):
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

    with (
        patch("agent.auxiliary_client._read_main_model", return_value="gpt-5.4"),
        patch.object(CopilotACPClient, "_create_chat_completion", return_value=fake_response) as mock_create,
        patch(
            "hermes_cli.auth.resolve_external_process_provider_credentials",
            return_value={
                "provider": "copilot-acp",
                "api_key": "copilot-acp",
                "base_url": "acp://copilot",
                "command": "/usr/bin/copilot",
                "args": ["--acp", "--stdio"],
            },
        ),
    ):
        model_name, content, success = await moa._run_reference_model_safe(
            {"model": "gpt-5.4", "provider": "copilot-acp", "reasoning_config": None},
            "hello",
            max_retries=1,
        )

    assert (model_name, content, success) == ("gpt-5.4", "ok", True)
    mock_create.assert_called_once()


def _static_provider_catalog(provider: str) -> list[str]:
    provider = models_mod.normalize_provider(provider)
    if provider == "openrouter":
        return [model_id for model_id, _ in models_mod.OPENROUTER_MODELS]
    return list(models_mod._PROVIDER_MODELS.get(provider, []))


def test_default_config_moa_entries_exist_in_provider_catalogs():
    default_moa = DEFAULT_CONFIG["moa"]
    entries = list(default_moa["reference_models"]) + [default_moa["aggregator_model"]]

    for entry in entries:
        normalized_model = normalize_model_for_provider(entry["model"], entry["provider"])
        assert normalized_model in _static_provider_catalog(entry["provider"])


def test_module_constant_fallbacks_exist_in_openrouter_catalog():
    openrouter_catalog = _static_provider_catalog("openrouter")
    for model in moa.REFERENCE_MODELS + [moa.AGGREGATOR_MODEL]:
        normalized_model = normalize_model_for_provider(model, "openrouter")
        assert normalized_model in openrouter_catalog


def test_debug_parameters_capture_provider_and_reasoning(fake_catalogs):
    _write_config(
        {
            "moa": {
                "reference_models": [
                    {
                        "model": "anthropic/claude-opus-4.7",
                        "provider": "openrouter",
                        "reasoning": "high",
                    },
                    {"model": "gpt-5.4", "provider": "openai-codex"},
                ],
                "aggregator_model": {
                    "model": "anthropic/claude-opus-4.7",
                    "provider": "ai-gateway",
                    "reasoning": "none",
                },
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


@pytest.mark.asyncio
async def test_mixture_of_agents_uses_adaptive_quorum(fake_catalogs, monkeypatch):
    _write_config(
        {
            "moa": {
                "reference_models": ["anthropic/claude-opus-4.7", "openai/gpt-5.5-pro"],
                "aggregator_model": "anthropic/claude-opus-4.7",
            }
        }
    )
    monkeypatch.setattr(
        moa,
        "_run_reference_model_safe",
        AsyncMock(side_effect=[
            ("anthropic/claude-opus-4.7", "ok", True),
            ("openai/gpt-5.5-pro", "failed", False),
        ]),
    )
    monkeypatch.setattr(moa, "_run_aggregator_model", AsyncMock(return_value="final"))
    monkeypatch.setattr(
        moa,
        "_debug",
        SimpleNamespace(log_call=MagicMock(), save=MagicMock(), active=False),
    )

    result = json.loads(await moa.mixture_of_agents_tool("solve this"))

    assert result["success"] is False
    assert "Need at least 2 successful responses" in result["error"]
