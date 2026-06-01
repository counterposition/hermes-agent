"""Cerebras Inference provider profile."""

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class CerebrasProfile(ProviderProfile):
    """Cerebras — OpenAI-compatible; reasoning models take top-level reasoning_effort."""

    @staticmethod
    def _reasoning_text(message: dict[str, Any]) -> str:
        for field in ("reasoning_content", "reasoning"):
            value = message.get(field)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    @staticmethod
    def _replay_content(model: str | None, reasoning: str, content: Any) -> Any:
        if not reasoning or not isinstance(content, (str, type(None))):
            return content
        answer = content or ""
        ml = (model or "").lower()
        if "glm" in ml:
            return f"<think>{reasoning}</think>{answer}"
        if "gpt-oss" in ml:
            return f"{reasoning}{answer}"
        return content

    def project_assistant_replay(
        self,
        source_msg: dict[str, Any],
        api_msg: dict[str, Any],
        *,
        model: str | None = None,
    ) -> bool:
        """Replay Cerebras reasoning in content, never in a sidecar field."""
        reasoning = self._reasoning_text(source_msg)
        source_content = source_msg.get("content")
        wire_content = api_msg.get("content")

        # An api_content sidecar is the authoritative byte-for-byte replay of
        # a previously sent/received assistant row.  Do not prepend reasoning
        # again when the loop has already substituted that distinct content.
        if wire_content == source_content:
            api_msg["content"] = self._replay_content(model, reasoning, wire_content)
        api_msg.pop("reasoning_content", None)
        api_msg.pop("reasoning", None)
        return True

    def prepare_messages_for_model(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        # Cerebras's chat-completions schema rejects ``reasoning_content`` on
        # assistant messages (HTTP 400 ``wrong_api_format``: "property
        # 'messages.N.assistant.reasoning_content' is unsupported"). Current
        # Cerebras replay guidance puts retained reasoning in assistant content,
        # not a non-standard message field.  This is a defense-in-depth path for
        # direct transport callers that bypass the agent replay projection.
        out = list(messages)
        for i, m in enumerate(out):
            if not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            reasoning = self._reasoning_text(m)
            if not reasoning and "reasoning_content" not in m and "reasoning" not in m:
                continue
            new_m = {
                k: v for k, v in m.items()
                if k not in ("reasoning_content", "reasoning")
            }
            new_m["content"] = self._replay_content(
                model, reasoning, new_m.get("content")
            )
            out[i] = new_m
        return out

    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Backward-compatible direct hook when no model context is available."""
        return self.prepare_messages_for_model(messages)

    def build_extra_body(
        self,
        *,
        model: str | None = None,
        reasoning_config: dict | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        # zai-glm-4.7 defaults to ``clear_thinking=true``, which DROPS reasoning
        # from previous turns out of the prompt context. For agentic / tool-
        # calling workflows Cerebras recommends ``clear_thinking=false``
        # ("Preserved Thinking") so provider-supported thinking history can be
        # retained across turns. The flag only applies to GLM (gpt-oss and
        # non-reasoning models reject / ignore it).
        #
        # GLM enables reasoning by default, so emit ``clear_thinking=false``
        # whenever reasoning is not *explicitly* disabled — including the common
        # default path where no reasoning_config is set (``reasoning_config`` is
        # ``None``).
        ml = (model or "").lower()
        if "glm" not in ml:
            return {}
        if isinstance(reasoning_config, dict) and reasoning_config.get("enabled") is False:
            return {}
        return {"clear_thinking": False}

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        model: str | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # gpt-oss accepts low/medium/high reasoning_effort. GLM only documents
        # ``none`` as an accepted value (to disable reasoning); enabled GLM
        # requests must omit the field. Other Cerebras models reject it. Cerebras is
        # not in _supports_reasoning_extra_body()'s allowlist, so this hook is
        # the only path that conveys reasoning effort for these models.
        ml = (model or "").lower()
        is_glm = "glm" in ml
        if "gpt-oss" not in ml and not is_glm:
            return {}, {}
        # Explicit disable: GLM keeps reasoning ON by default, so it is only
        # actually turned off by sending ``reasoning_effort="none"`` — omitting
        # the field is NOT a disable. gpt-oss has no documented "none" level, so
        # leave it untouched (omitting falls back to the model default).
        if isinstance(reasoning_config, dict) and reasoning_config.get("enabled") is False:
            return ({}, {"reasoning_effort": "none"}) if is_glm else ({}, {})
        if is_glm:
            return {}, {}
        if not isinstance(reasoning_config, dict):
            return {}, {}
        effort = (reasoning_config.get("effort") or "").strip().lower()
        if effort in ("xhigh", "max", "ultra"):
            effort = "high"  # Cerebras tops out at "high"
        if effort not in ("low", "medium", "high"):
            effort = "medium"
        return {}, {"reasoning_effort": effort}


cerebras = CerebrasProfile(
    name="cerebras",
    env_vars=("CEREBRAS_API_KEY",),
    display_name="Cerebras",
    description="Cerebras — ultra-fast wafer-scale inference (OpenAI-compatible)",
    signup_url="https://cloud.cerebras.ai/",
    base_url="https://api.cerebras.ai/v1",
    auth_type="api_key",
    default_aux_model="gpt-oss-120b",
    fallback_models=(
        "gpt-oss-120b",
        "zai-glm-4.7",
    ),
)

register_provider(cerebras)
