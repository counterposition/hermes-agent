#!/usr/bin/env python3
"""Mixture-of-Agents Tool Module.

Runs a hard prompt through multiple frontier LLMs in parallel (layer 1:
reference models), then synthesizes their responses with an aggregator
model (layer 2).  Per-model provider routing and reasoning effort are
configured via ``config.yaml`` under the ``moa`` key; see
``hermes_cli.config.DEFAULT_CONFIG`` for the default shape.  The module
constants below are fallback defaults used only when the ``moa`` block
is absent or incomplete.
"""

import asyncio
import datetime
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from agent.auxiliary_client import (
    extract_content_or_reasoning,
    resolve_provider_client,
)
from tools.debug_helpers import DebugSession
from tools.openrouter_client import check_api_key as check_openrouter_api_key

logger = logging.getLogger(__name__)

# Fallback defaults — used only when the `moa` config block is absent or
# incomplete.  Mirrors DEFAULT_CONFIG["moa"] so upstream rotations flow in
# cleanly via merge.  Configure via config.yaml; don't edit here.
REFERENCE_MODELS = [
    "anthropic/claude-opus-4.7",
    "google/gemini-3.1-pro-preview",
    "openai/gpt-5.5-pro",
    "qwen/qwen3.6-plus",
]
AGGREGATOR_MODEL = "anthropic/claude-opus-4.7"
REFERENCE_TEMPERATURE = 0.6
AGGREGATOR_TEMPERATURE = 0.4
MIN_SUCCESSFUL_REFERENCES = 2

AGGREGATOR_SYSTEM_PROMPT = """You have been provided with a set of responses from various frontier models to the latest user query. Your task is to synthesize these responses into a single, high-quality response. It is crucial to critically evaluate the information provided in these responses, recognizing that some of it may be biased or incorrect. Your response should not simply replicate the given answers but should offer a refined, accurate, and comprehensive reply to the instruction. Ensure your response is well-structured, coherent, and adheres to the highest standards of accuracy and reliability.

Responses from models:"""

_debug = DebugSession("moa_tools", env_var="MOA_TOOLS_DEBUG")


# One-shot Codex plan-consumption warnings.  Re-fires when the set of
# Codex-routed slots changes (e.g. profile switch flips the aggregator).
_codex_warning_seen: set = set()
_reasoning_skip_warning_seen: set[Tuple[str, str]] = set()

# PR1 only forwards reasoning through providers that accept
# OpenAI-compatible ``extra_body.reasoning`` or already translate it in
# Hermes' auxiliary adapter. Provider-specific kwargs land in PR2.
_REASONING_EXTRA_BODY_PROVIDERS = frozenset({
    "openrouter",
    "nous",
    "ai-gateway",
    "openai-codex",
})


def _effort_label(entry: Dict[str, Any]) -> str:
    """Return the raw reasoning label for logs / debug output."""
    rc = entry.get("reasoning_config")
    if rc is None:
        return "default"
    if rc.get("enabled") is False:
        return "none"
    return rc.get("effort") or "default"


# ---------------------------------------------------------------------------
# Config loading + validation
# ---------------------------------------------------------------------------
def _normalize_entry(entry: Any, idx: Optional[int], is_aggregator: bool) -> Dict[str, Any]:
    """Expand string shorthand / validate dict shape.  Returns {model, provider, _raw_reasoning}."""
    if is_aggregator:
        where = "moa.aggregator_model"
    else:
        where = f"moa.reference_models[{idx}]"

    if isinstance(entry, str):
        model = entry.strip()
        if not model:
            raise ValueError(f"{where} must specify a non-empty model")
        return {"model": model, "provider": "openrouter", "_raw_reasoning": None}

    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a string or a dict")

    model = entry.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"{where} must specify a 'model' field")
    model = model.strip()

    provider = entry.get("provider") or "openrouter"
    if not isinstance(provider, str):
        raise ValueError(f"{where}.provider must be a string")
    try:
        from hermes_cli.models import normalize_provider
    except ImportError:
        provider = provider.strip().lower()
    else:
        provider = normalize_provider(provider)

    raw_reasoning = entry.get("reasoning")
    return {
        "model": model,
        "provider": provider,
        "_raw_reasoning": raw_reasoning,
    }


def _read_raw_moa_subtree() -> Dict[str, Any]:
    """Re-read the YAML config for pre-merge empty-dict override detection.

    ``_deep_merge`` recurses into dict-typed fields and preserves the default
    when the override is ``{}``, so ``aggregator_model: {}`` is invisible
    against the merged dict.  We load the raw file to catch that typo.
    """
    import yaml
    from hermes_cli.config import get_config_path

    path = get_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(
            f"unable to read raw moa config for validation: {exc}"
        ) from exc
    if not isinstance(data, dict):
        return {}
    return data.get("moa") or {}


def _load_moa_config(emit_warnings: bool = False) -> Dict[str, Any]:
    """Load and validate the ``moa`` config block.

    Args:
        emit_warnings: When True, emit operational-guidance logs (Codex
            plan-consumption warnings, catalog-drift soft warnings).
            Suppressed during preflight and startup.

    Returns:
        Dict with keys: enabled, reference_models, aggregator_model,
        reference_temperature, aggregator_temperature,
        min_successful_references.  Reference/aggregator entries carry
        {model, provider, reasoning_config}.

    Raises:
        ValueError: On shape errors, unknown providers, invalid reasoning
        strings, out-of-range min_successful_references, or empty rosters.
        Raised even when ``enabled`` is False so toggling back on is safe.
    """
    from hermes_cli.config import load_config
    from hermes_cli.models import list_available_providers, provider_model_ids
    from hermes_constants import VALID_REASONING_EFFORTS, parse_reasoning_effort

    config = load_config()
    moa_value = config.get("moa", {})
    if moa_value is None:
        moa = {}
    elif not isinstance(moa_value, dict):
        raise ValueError("moa must be a mapping")
    else:
        moa = moa_value
    raw_moa = _read_raw_moa_subtree()
    raw_moa_dict = raw_moa if isinstance(raw_moa, dict) else {}

    # Kill switch
    enabled_val = moa.get("enabled", True)
    if not isinstance(enabled_val, bool):
        raise ValueError("moa.enabled must be a boolean")
    enabled = enabled_val

    # Pre-merge empty-dict detection (raw YAML re-read; see _read_raw_moa_subtree)
    if raw_moa_dict:
        raw_agg = raw_moa_dict.get("aggregator_model")
        if isinstance(raw_agg, dict) and raw_agg == {}:
            raise ValueError("moa.aggregator_model must specify a 'model' field")
        raw_refs = raw_moa_dict.get("reference_models")
        if isinstance(raw_refs, list):
            for i, item in enumerate(raw_refs):
                if isinstance(item, dict) and item == {}:
                    raise ValueError(
                        f"moa.reference_models[{i}] must specify a 'model' field"
                    )

    # Reference models
    missing = object()
    if "reference_models" in raw_moa_dict:
        ref_raw = raw_moa_dict["reference_models"]
    elif "reference_models" in moa:
        ref_raw = moa["reference_models"]
    else:
        ref_raw = missing

    if ref_raw is not missing:
        if ref_raw is None:
            raise ValueError("moa.reference_models must be a non-empty list")
        if not isinstance(ref_raw, list) or len(ref_raw) == 0:
            raise ValueError("moa.reference_models must be a non-empty list")
        ref_entries = [
            _normalize_entry(e, idx=i, is_aggregator=False)
            for i, e in enumerate(ref_raw)
        ]
    else:
        ref_entries = [
            {"model": m, "provider": "openrouter", "_raw_reasoning": None}
            for m in REFERENCE_MODELS
        ]

    # Aggregator
    if "aggregator_model" in raw_moa_dict:
        agg_raw = raw_moa_dict["aggregator_model"]
    elif "aggregator_model" in moa:
        agg_raw = moa["aggregator_model"]
    else:
        agg_raw = missing

    if agg_raw is not missing:
        if agg_raw is None:
            raise ValueError("moa.aggregator_model must specify a 'model' field")
        agg_entry = _normalize_entry(agg_raw, idx=None, is_aggregator=True)
    else:
        agg_entry = {
            "model": AGGREGATOR_MODEL,
            "provider": "openrouter",
            "_raw_reasoning": None,
        }

    # Valid provider ids (from the canonical list + named custom providers)
    valid_providers = {p["id"] for p in list_available_providers()}
    try:
        from hermes_cli.config import get_compatible_custom_providers
        from hermes_cli.providers import custom_provider_slug
    except ImportError:
        custom_provider_slugs = set()
    else:
        custom_provider_slugs = {
            custom_provider_slug(str(entry.get("name") or ""))
            for entry in get_compatible_custom_providers(config)
            if isinstance(entry, dict) and str(entry.get("name") or "").strip()
        }

    for e in ref_entries + [agg_entry]:
        prov = e["provider"]
        if prov in custom_provider_slugs:
            continue
        if prov not in valid_providers:
            raise ValueError(
                f"moa: unknown provider {prov!r}; "
                f"valid ids: {sorted(valid_providers | custom_provider_slugs)}"
            )

    # Reasoning per entry (accepts unset/null/'' as "use provider default")
    valid_reasoning_values = (*VALID_REASONING_EFFORTS, "none")
    for e in ref_entries + [agg_entry]:
        raw = e.pop("_raw_reasoning", None)
        if raw is None:
            e["reasoning_config"] = None
            continue
        if isinstance(raw, str) and raw.strip() == "":
            e["reasoning_config"] = None
            continue
        if not isinstance(raw, str):
            raise ValueError(
                f"moa: reasoning must be a string; valid: "
                f"{list(valid_reasoning_values)}"
            )
        normalized_raw = raw.strip().lower()
        if normalized_raw not in valid_reasoning_values:
            raise ValueError(
                f"moa: invalid reasoning {raw!r}; valid: "
                f"{list(valid_reasoning_values)}"
            )
        parsed = parse_reasoning_effort(raw)
        if parsed is None:
            raise ValueError(
                f"moa: invalid reasoning {raw!r}; valid: "
                f"{list(valid_reasoning_values)}"
            )
        e["reasoning_config"] = parsed

    # Temperatures
    ref_temp = moa.get("reference_temperature", REFERENCE_TEMPERATURE)
    agg_temp = moa.get("aggregator_temperature", AGGREGATOR_TEMPERATURE)

    # min_successful_references (adaptive default)
    if "min_successful_references" in moa:
        msr = moa["min_successful_references"]
        if isinstance(msr, bool) or not isinstance(msr, int):
            raise ValueError("moa.min_successful_references must be an integer")
        if msr < 1 or msr > len(ref_entries):
            raise ValueError(
                f"moa.min_successful_references must be between 1 and "
                f"{len(ref_entries)} (got {msr})"
            )
    else:
        msr = min(2, len(ref_entries))

    # Soft catalog-drift warning (emit_warnings gates this; no raise)
    if emit_warnings:
        for e in ref_entries + [agg_entry]:
            try:
                catalog = provider_model_ids(e["provider"])
            except Exception:
                catalog = []
            if catalog and e["model"] not in catalog:
                logger.warning(
                    "MoA: model %r not in %s catalog — may fail at call time",
                    e["model"], e["provider"],
                )

    # Codex plan warning
    codex_slots = [
        e["model"] for e in ref_entries
        if e["provider"] == "openai-codex"
    ]
    aggregator_is_codex = agg_entry["provider"] == "openai-codex"
    if aggregator_is_codex:
        codex_slots.append(agg_entry["model"])
    if emit_warnings and codex_slots:
        key = (frozenset(codex_slots), aggregator_is_codex)
        if key not in _codex_warning_seen:
            _codex_warning_seen.add(key)
            msg_parts = [
                "MoA: %d slot(s) routed through "
                "openai-codex; each invocation consumes a Codex plan request"
                % len(codex_slots)
            ]
            if aggregator_is_codex:
                msg_parts.append(
                    "aggregator is Codex-routed — aggregator failure fails "
                    "the whole MoA call"
                )
            logger.warning("; ".join(msg_parts))

    return {
        "enabled": enabled,
        "reference_models": ref_entries,
        "aggregator_model": agg_entry,
        "reference_temperature": ref_temp,
        "aggregator_temperature": agg_temp,
        "min_successful_references": msr,
        "min_successful_references_explicit": "min_successful_references" in moa,
    }


# ---------------------------------------------------------------------------
# Model runners
# ---------------------------------------------------------------------------
def _construct_aggregator_prompt(system_prompt: str, responses: List[str]) -> str:
    response_text = "\n".join(
        f"{i+1}. {response}" for i, response in enumerate(responses)
    )
    return f"{system_prompt}\n\n{response_text}"


async def _create_chat_completion(client, **kwargs):
    """Create a chat completion with async and sync client compatibility."""
    import inspect

    create = client.chat.completions.create
    if inspect.iscoroutinefunction(create):
        return await create(**kwargs)
    return await asyncio.to_thread(create, **kwargs)


def _build_api_params(
    provider: str,
    resolved_model: str,
    messages: List[Dict[str, Any]],
    reasoning_config: Optional[dict],
    temperature: float,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Assemble the minimal PR1 chat.completions kwargs."""
    params: Dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "temperature": temperature,
    }
    if reasoning_config is not None:
        if provider in _REASONING_EXTRA_BODY_PROVIDERS:
            params["extra_body"] = {"reasoning": reasoning_config}
        else:
            warning_key = (provider, resolved_model)
            if warning_key not in _reasoning_skip_warning_seen:
                _reasoning_skip_warning_seen.add(warning_key)
                logger.warning(
                    "MoA: reasoning for provider %r is not forwarded in PR1; "
                    "omit moa.*.reasoning for this provider or rely on "
                    "provider defaults until provider-specific routing lands",
                    provider,
                )
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    return params


async def _run_reference_model_safe(
    model_entry: Dict[str, Any],
    user_prompt: str,
    temperature: float = REFERENCE_TEMPERATURE,
    max_tokens: int = 32000,
    max_retries: int = 6,
) -> Tuple[str, str, bool]:
    """Call a single reference model with retry handling."""
    model = model_entry["model"]
    provider = model_entry["provider"]
    reasoning_config = model_entry.get("reasoning_config")

    try:
        client, resolved_model = resolve_provider_client(
            provider, model=model, async_mode=True
        )
    except Exception as exc:  # noqa: BLE001 — init failure is per-model, not fatal
        err = f"{model} could not be initialized: {exc}"
        logger.error("%s", err, exc_info=True)
        return model, err, False
    if client is None:
        err = f"{model}: no credentials configured for provider {provider!r}"
        logger.error("%s", err)
        return model, err, False

    messages = [{"role": "user", "content": user_prompt}]
    logger.info(
        "MoA reference %s via %s (resolved=%s, reasoning=%s)",
        model, provider, resolved_model, _effort_label(model_entry),
    )

    attempt = 0
    while attempt < max_retries:
        try:
            api_params = _build_api_params(
                provider,
                resolved_model,
                messages,
                reasoning_config,
                temperature,
                max_tokens=max_tokens,
            )
            response = await _create_chat_completion(client, **api_params)

            content = extract_content_or_reasoning(response)
            if not content:
                logger.warning(
                    "%s returned empty content (attempt %s/%s), retrying",
                    model, attempt + 1, max_retries,
                )
                attempt += 1
                if attempt < max_retries:
                    await asyncio.sleep(min(2 ** attempt, 60))
                continue
            logger.info("%s responded (%s characters)", model, len(content))
            return model, content, True

        except Exception as e:  # noqa: BLE001
            error_str = str(e)
            if "invalid" in error_str.lower():
                logger.warning("%s invalid request error (attempt %s): %s", model, attempt + 1, error_str)
            elif "rate" in error_str.lower() or "limit" in error_str.lower():
                logger.warning("%s rate limit error (attempt %s): %s", model, attempt + 1, error_str)
            else:
                logger.warning("%s error (attempt %s): %s", model, attempt + 1, error_str)

            attempt += 1
            if attempt < max_retries:
                sleep_time = min(2 ** attempt, 60)
                logger.info("Retrying %s in %ss...", model, sleep_time)
                await asyncio.sleep(sleep_time)
            else:
                err = f"{model} failed after {max_retries} attempts: {error_str}"
                logger.error("%s", err, exc_info=True)
                return model, err, False

    return model, f"{model} exhausted retries", False


async def _run_aggregator_model(
    agg_entry: Dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    temperature: float = AGGREGATOR_TEMPERATURE,
    max_tokens: Optional[int] = None,
    max_retries: int = 3,
) -> str:
    """Call the aggregator with retry handling."""
    model = agg_entry["model"]
    provider = agg_entry["provider"]
    reasoning_config = agg_entry.get("reasoning_config")

    client, resolved_model = resolve_provider_client(
        provider, model=model, async_mode=True
    )
    if client is None:
        raise ValueError(
            f"MoA aggregator: no credentials configured for provider {provider!r}"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    logger.info(
        "MoA aggregator %s via %s (resolved=%s, reasoning=%s)",
        model, provider, resolved_model, _effort_label(agg_entry),
    )

    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            api_params = _build_api_params(
                provider,
                resolved_model,
                messages,
                reasoning_config,
                temperature,
                max_tokens=max_tokens,
            )
            response = await _create_chat_completion(client, **api_params)
            content = extract_content_or_reasoning(response)
            if not content and attempt < max_retries - 1:
                logger.warning("Aggregator returned empty content, retrying")
                continue
            logger.info("Aggregation complete (%s characters)", len(content) if content else 0)
            return content or ""
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.warning("Aggregator error (attempt %s): %s", attempt + 1, e)
            if attempt < max_retries - 1:
                await asyncio.sleep(min(2 ** (attempt + 1), 30))

    raise RuntimeError(f"MoA aggregator failed after {max_retries} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _override_entries_from_strings(
    reference_models: Optional[List[str]],
    aggregator_model: Optional[str],
    cfg: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Apply optional string-only per-invocation overrides (OpenRouter-only)."""
    ref_entries = cfg["reference_models"]
    agg_entry = cfg["aggregator_model"]
    if reference_models is not None:
        ref_entries = [
            {"model": m.strip(), "provider": "openrouter", "reasoning_config": None}
            for m in reference_models
            if isinstance(m, str) and m.strip()
        ]
    if aggregator_model is not None:
        agg_entry = {
            "model": aggregator_model.strip(),
            "provider": "openrouter",
            "reasoning_config": None,
        }
    return ref_entries, agg_entry


def _effective_min_successful_references(
    cfg: Dict[str, Any],
    ref_entries: List[Dict[str, Any]],
) -> int:
    """Return the effective quorum after per-call roster overrides."""
    if not ref_entries:
        raise ValueError("MoA requires at least one reference model")

    configured = cfg["min_successful_references"]
    if cfg.get("min_successful_references_explicit"):
        if configured > len(ref_entries):
            raise ValueError(
                "MoA per-call overrides reduced the reference roster to "
                f"{len(ref_entries)} model(s), but "
                f"moa.min_successful_references is pinned to {configured}. "
                "Lower the config quorum or remove the override."
            )
        return configured
    return min(2, len(ref_entries))


async def mixture_of_agents_tool(
    user_prompt: str,
    reference_models: Optional[List[str]] = None,
    aggregator_model: Optional[str] = None,
) -> str:
    """Run a prompt through multiple frontier LLMs and synthesize the responses.

    Configuration is loaded from ``config.yaml`` (the ``moa`` block) on every
    invocation so profile switches take effect without a restart.  Per-model
    provider routing and reasoning effort live in config; ``reference_models``
    and ``aggregator_model`` parameters are OpenRouter-only string overrides
    retained for backward compatibility.
    """
    start_time = datetime.datetime.now()

    try:
        cfg = _load_moa_config(emit_warnings=True)
    except ValueError as exc:
        error_msg = f"MoA config error: {exc}"
        logger.error("%s", error_msg)
        return json.dumps({
            "success": False,
            "response": "",
            "models_used": {"reference_models": [], "aggregator_model": ""},
            "error": error_msg,
        }, indent=2, ensure_ascii=False)

    # Kill switch: fail-closed.  Callers branching on ``success`` route to
    # their normal error path; ``error`` string is distinct from real failures.
    if not cfg["enabled"]:
        logger.info("MoA disabled via config; short-circuiting without calling any model")
        return json.dumps({
            "success": False,
            "response": "",
            "models_used": {"reference_models": [], "aggregator_model": ""},
            "error": "MoA disabled via moa.enabled=false",
        }, indent=2, ensure_ascii=False)

    ref_entries, agg_entry = _override_entries_from_strings(
        reference_models, aggregator_model, cfg,
    )
    ref_temp = cfg["reference_temperature"]
    agg_temp = cfg["aggregator_temperature"]

    debug_call_data: Dict[str, Any] = {
        "parameters": {
            "user_prompt": user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt,
            "reference_models": [
                {"model": e["model"], "provider": e["provider"], "reasoning": _effort_label(e)}
                for e in ref_entries
            ],
            "aggregator_model": {
                "model": agg_entry["model"],
                "provider": agg_entry["provider"],
                "reasoning": _effort_label(agg_entry),
            },
            "reference_temperature": ref_temp,
            "aggregator_temperature": agg_temp,
            "min_successful_references": None,
        },
        "error": None,
        "success": False,
        "reference_responses_count": 0,
        "failed_models_count": 0,
        "failed_models": [],
        "final_response_length": 0,
        "processing_time_seconds": 0,
        "models_used": {},
    }

    try:
        msr = _effective_min_successful_references(cfg, ref_entries)
        debug_call_data["parameters"]["min_successful_references"] = msr

        logger.info("Starting Mixture-of-Agents processing...")
        logger.info("Query: %s", user_prompt[:100])
        logger.info("Using %s reference models in 2-layer MoA architecture", len(ref_entries))

        # Layer 1: parallel fanout
        logger.info("Layer 1: Generating reference responses...")
        model_results = await asyncio.gather(*[
            _run_reference_model_safe(entry, user_prompt, ref_temp)
            for entry in ref_entries
        ])

        successful_responses: List[str] = []
        failed_models: List[str] = []
        for model_name, content, success in model_results:
            if success:
                successful_responses.append(content)
            else:
                failed_models.append(model_name)

        successful_count = len(successful_responses)
        failed_count = len(failed_models)
        logger.info("Reference model results: %s successful, %s failed", successful_count, failed_count)
        if failed_models:
            logger.warning("Failed models: %s", ", ".join(failed_models))

        if successful_count < msr:
            raise ValueError(
                f"Insufficient successful reference models "
                f"({successful_count}/{len(ref_entries)}). "
                f"Need at least {msr} successful responses."
            )

        debug_call_data["reference_responses_count"] = successful_count
        debug_call_data["failed_models_count"] = failed_count
        debug_call_data["failed_models"] = failed_models

        # Layer 2: aggregation
        logger.info("Layer 2: Synthesizing final response...")
        aggregator_system_prompt = _construct_aggregator_prompt(
            AGGREGATOR_SYSTEM_PROMPT, successful_responses,
        )
        final_response = await _run_aggregator_model(
            agg_entry, aggregator_system_prompt, user_prompt, agg_temp,
        )

        end_time = datetime.datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        logger.info("MoA processing completed in %.2f seconds", processing_time)

        result = {
            "success": True,
            "response": final_response,
            "models_used": {
                "reference_models": [e["model"] for e in ref_entries],
                "aggregator_model": agg_entry["model"],
            },
        }

        debug_call_data["success"] = True
        debug_call_data["final_response_length"] = len(final_response)
        debug_call_data["processing_time_seconds"] = processing_time
        debug_call_data["models_used"] = result["models_used"]

        _debug.log_call("mixture_of_agents_tool", debug_call_data)
        _debug.save()
        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:  # noqa: BLE001
        error_msg = f"Error in MoA processing: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)
        end_time = datetime.datetime.now()
        processing_time = (end_time - start_time).total_seconds()

        result = {
            "success": False,
            "response": "MoA processing failed. Please try again or use a single model for this query.",
            "models_used": {
                "reference_models": [e["model"] for e in ref_entries],
                "aggregator_model": agg_entry["model"],
            },
            "error": error_msg,
        }
        debug_call_data["error"] = error_msg
        debug_call_data["processing_time_seconds"] = processing_time
        _debug.log_call("mixture_of_agents_tool", debug_call_data)
        _debug.save()
        return json.dumps(result, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Preflight / introspection
# ---------------------------------------------------------------------------
def _provider_has_credentials(provider: str, model: Optional[str] = None) -> bool:
    """Return True when the runtime provider resolver can create a client."""
    try:
        client, _ = resolve_provider_client(
            provider,
            model=model,
            async_mode=False,
        )
    except Exception:
        return False
    if client is None:
        return False
    try:
        close_fn = getattr(client, "close", None)
        if callable(close_fn):
            close_fn()
    except Exception:
        pass
    return True


def get_moa_preflight_status() -> Tuple[bool, Optional[str]]:
    """Return (available, hint) for setup/preflight surfaces.

    The hint is phrased to fit setup output like ``(missing <hint>)``.
    """
    try:
        cfg = _load_moa_config(emit_warnings=False)
    except ValueError as exc:
        logger.error("MoA config invalid: %s", exc)
        return False, "valid MoA config"

    if not cfg["enabled"]:
        # Hide from preflight-gated menus when the kill switch is flipped.
        return False, "enabled MoA config"

    entries = list(cfg["reference_models"]) + [cfg["aggregator_model"]]
    missing = sorted({
        entry["provider"]
        for entry in entries
        if not _provider_has_credentials(entry["provider"], model=entry["model"])
    })
    if missing:
        logger.error(
            "MoA: missing credentials for provider(s): %s",
            ", ".join(missing),
        )
        return False, f"credentials for {', '.join(missing)}"
    return True, None


def check_moa_requirements() -> bool:
    """Preflight: return True iff all configured providers have credentials."""
    available, _ = get_moa_preflight_status()
    return available


def get_available_models() -> List[str]:
    """Return the module-constant fallback reference models.

    Reflects fallback defaults, not the resolved roster.
    """
    return list(REFERENCE_MODELS)


def get_moa_configuration() -> Dict[str, Any]:
    """Return module-constant fallback configuration.

    Reflects fallback defaults, not the resolved roster from config.yaml.
    """
    return {
        "reference_models": REFERENCE_MODELS,
        "aggregator_model": AGGREGATOR_MODEL,
        "reference_temperature": REFERENCE_TEMPERATURE,
        "aggregator_temperature": AGGREGATOR_TEMPERATURE,
        "min_successful_references": MIN_SUCCESSFUL_REFERENCES,
        "total_reference_models": len(REFERENCE_MODELS),
        "failure_tolerance": f"{len(REFERENCE_MODELS) - MIN_SUCCESSFUL_REFERENCES}/{len(REFERENCE_MODELS)} models can fail",
    }


if __name__ == "__main__":
    print("🤖 Mixture-of-Agents Tool Module")
    print("=" * 50)

    api_available = check_openrouter_api_key()
    if not api_available:
        print("⚠️  OPENROUTER_API_KEY not set — OpenRouter-routed entries will fail")
    else:
        print("✅ OpenRouter API key found")

    print("🛠️  MoA tools ready for use!")
    config = get_moa_configuration()
    print("\n⚙️  Fallback constants (config.yaml overrides these):")
    print(f"  🤖 Reference models ({len(config['reference_models'])}): {', '.join(config['reference_models'])}")
    print(f"  🧠 Aggregator model: {config['aggregator_model']}")
    print(f"  🌡️  Reference temperature: {config['reference_temperature']}")
    print(f"  🌡️  Aggregator temperature: {config['aggregator_temperature']}")
    print(f"  🛡️  Failure tolerance: {config['failure_tolerance']}")
    print(f"  📊 Minimum successful models: {config['min_successful_references']}")

    if _debug.active:
        print(f"\n🐛 Debug mode ENABLED - Session ID: {_debug.session_id}")
        print(f"   Debug logs will be saved to: ./logs/moa_tools_debug_{_debug.session_id}.json")
    else:
        print("\n🐛 Debug mode disabled (set MOA_TOOLS_DEBUG=true to enable)")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry  # noqa: E402

MOA_SCHEMA = {
    "name": "mixture_of_agents",
    "description": "Route a hard problem through multiple frontier LLMs collaboratively. Fans out to a user-configured roster of reference models in parallel, then synthesizes their answers with an aggregator model (N reference calls + 1 aggregator call; roster size, providers, and per-entry reasoning are set under `moa:` in config.yaml). Use sparingly for genuinely difficult problems. Best for: complex math, advanced algorithms, multi-step analytical reasoning, problems benefiting from diverse perspectives.",
    "parameters": {
        "type": "object",
        "properties": {
            "user_prompt": {
                "type": "string",
                "description": "The complex query or problem to solve using multiple AI models. Should be a challenging problem that benefits from diverse perspectives and collaborative reasoning."
            }
        },
        "required": ["user_prompt"]
    }
}

registry.register(
    name="mixture_of_agents",
    toolset="moa",
    schema=MOA_SCHEMA,
    handler=lambda args, **kw: mixture_of_agents_tool(user_prompt=args.get("user_prompt", "")),
    check_fn=check_moa_requirements,
    requires_env=[],
    is_async=True,
    emoji="🧠",
)
