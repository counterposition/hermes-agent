"""Cerebras reasoning compatibility.

Cerebras's OpenAI-compatible chat-completions schema rejects
``reasoning_content`` on assistant messages (HTTP 400 ``wrong_api_format``:
"property 'messages.N.assistant.reasoning_content' is unsupported"). Current
Cerebras replay guidance puts prior reasoning back into assistant ``content``:
GLM uses a ``<think>`` block and GPT-OSS prepends the reasoning text. GLM also
uses ``clear_thinking=false`` for agentic workflows.

These tests pin the provider-local compatibility shims that let multi-turn /
tool-calling conversations on ``cerebras`` survive past the first turn.
"""

import pytest

from agent.agent_runtime_helpers import copy_reasoning_content_for_api
from providers import get_provider_profile


@pytest.fixture
def profile():
    return get_provider_profile("cerebras")


class TestCerebrasReasoningReplay:
    def test_glm_reasoning_content_moves_into_content(self, profile):
        out = profile.prepare_messages_for_model(
            [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "hello",
                    "reasoning_content": "the user greeted me",
                },
            ],
            model="zai-glm-4.7",
        )
        assert "reasoning_content" not in out[1]
        assert "reasoning" not in out[1]
        assert out[1]["content"] == "<think>the user greeted me</think>hello"

    def test_gpt_oss_reasoning_is_prepended_to_content(self, profile):
        out = profile.prepare_messages_for_model(
            [{"role": "assistant", "content": "answer", "reasoning": "thought"}],
            model="gpt-oss-120b",
        )
        assert out == [{"role": "assistant", "content": "thoughtanswer"}]

    def test_strip_preserves_tool_calls(self, profile):
        out = profile.prepare_messages_for_model(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "need a tool",
                    "tool_calls": [{"id": "c1", "type": "function"}],
                }
            ],
            model="zai-glm-4.7",
        )
        assert "reasoning_content" not in out[0]
        assert "reasoning" not in out[0]
        assert out[0]["content"] == "<think>need a tool</think>"
        assert out[0]["tool_calls"] == [{"id": "c1", "type": "function"}]

    @pytest.mark.parametrize("blank", ["", "   ", "\n"])
    def test_blank_reasoning_content_dropped_not_renamed(self, profile, blank):
        out = profile.prepare_messages_for_model(
            [{"role": "assistant", "content": "x", "reasoning_content": blank}],
            model="zai-glm-4.7",
        )
        assert "reasoning_content" not in out[0]
        assert "reasoning" not in out[0]

    def test_non_string_reasoning_content_dropped(self, profile):
        out = profile.prepare_messages_for_model(
            [{"role": "assistant", "content": "x", "reasoning_content": None}],
            model="zai-glm-4.7",
        )
        assert "reasoning_content" not in out[0]
        assert "reasoning" not in out[0]

    def test_user_system_tool_messages_untouched(self, profile):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u"},
            {"role": "tool", "content": "t", "tool_call_id": "c1"},
        ]
        assert profile.prepare_messages(msgs) == msgs

    def test_does_not_mutate_input(self, profile):
        src = {"role": "assistant", "content": "hi", "reasoning_content": "thought"}
        profile.prepare_messages_for_model([src], model="zai-glm-4.7")
        # Stored history is reused for trajectory + other providers; leave it intact.
        assert src["reasoning_content"] == "thought"
        assert "reasoning" not in src

    def test_passthrough_when_no_reasoning_present(self, profile):
        out = profile.prepare_messages([{"role": "assistant", "content": "hello"}])
        assert out[0] == {"role": "assistant", "content": "hello"}

    def test_api_content_projection_is_not_double_prefixed(self, profile):
        source = {
            "role": "assistant",
            "content": "clean answer",
            "api_content": "<think>thought</think>clean answer",
            "reasoning": "thought",
        }
        wire = dict(source)
        wire.pop("api_content")
        wire["content"] = source["api_content"]

        assert profile.project_assistant_replay(
            source, wire, model="zai-glm-4.7"
        ) is True
        assert wire["content"] == source["api_content"]
        assert "reasoning" not in wire

    def test_agent_replay_builder_delegates_to_cerebras_profile(self):
        class Agent:
            provider = "cerebras"
            model = "zai-glm-4.7"

        source = {
            "role": "assistant",
            "content": "answer",
            "reasoning": "thought",
            "reasoning_content": "native thought",
        }
        wire = dict(source)

        copy_reasoning_content_for_api(Agent(), source, wire)

        assert wire == {
            "role": "assistant",
            "content": "<think>native thought</think>answer",
        }
        assert source["reasoning"] == "thought"
        assert source["reasoning_content"] == "native thought"


class TestCerebrasPreservedThinking:
    def test_glm_sets_clear_thinking_false_when_reasoning_enabled(self, profile):
        body = profile.build_extra_body(
            model="zai-glm-4.7", reasoning_config={"enabled": True, "effort": "medium"}
        )
        assert body == {"clear_thinking": False}

    def test_glm_reasoning_explicitly_disabled_no_clear_thinking(self, profile):
        body = profile.build_extra_body(
            model="zai-glm-4.7", reasoning_config={"enabled": False}
        )
        assert body == {}

    def test_glm_no_reasoning_config_still_preserves_thinking(self, profile):
        # Default path: no explicit reasoning_config. GLM reasons by default, so
        # we must still send clear_thinking=false. This guards the default path,
        # where no populated reasoning_config dict reaches the profile.
        assert profile.build_extra_body(model="zai-glm-4.7", reasoning_config=None) == {
            "clear_thinking": False
        }

    def test_glm_reasoning_config_without_enabled_key_preserves_thinking(self, profile):
        # enabled is implied unless explicitly set to False.
        assert profile.build_extra_body(
            model="zai-glm-4.7", reasoning_config={"effort": "medium"}
        ) == {"clear_thinking": False}

    def test_gpt_oss_does_not_get_clear_thinking(self, profile):
        body = profile.build_extra_body(
            model="gpt-oss-120b", reasoning_config={"enabled": True, "effort": "low"}
        )
        assert body == {}

    def test_non_reasoning_model_no_clear_thinking(self, profile):
        body = profile.build_extra_body(
            model="llama-3.3-70b", reasoning_config={"enabled": True}
        )
        assert body == {}


class TestCerebrasReasoningEffort:
    """The reasoning_effort hook remains intact alongside message cleanup."""

    @pytest.mark.parametrize("effort", ["xhigh", "max", "ultra"])
    def test_gpt_oss_reasoning_effort_clamped(self, profile, effort):
        extra_body, top_level = profile.build_api_kwargs_extras(
            model="gpt-oss-120b", reasoning_config={"enabled": True, "effort": effort}
        )
        assert top_level == {"reasoning_effort": "high"}

    @pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
    def test_glm_enabled_omits_unsupported_effort(self, profile, effort):
        assert profile.build_api_kwargs_extras(
            model="zai-glm-4.7",
            reasoning_config={"enabled": True, "effort": effort},
        ) == ({}, {})

    def test_non_reasoning_model_no_effort(self, profile):
        extra_body, top_level = profile.build_api_kwargs_extras(
            model="llama-3.3-70b", reasoning_config={"enabled": True, "effort": "high"}
        )
        assert top_level == {}

    def test_glm_explicit_disable_sends_reasoning_effort_none(self, profile):
        # Cerebras GLM stays reasoning-ON unless reasoning_effort="none" is sent;
        # omitting the field is not a disable.
        extra_body, top_level = profile.build_api_kwargs_extras(
            model="zai-glm-4.7", reasoning_config={"enabled": False}
        )
        assert (extra_body, top_level) == ({}, {"reasoning_effort": "none"})

    def test_gpt_oss_explicit_disable_sends_nothing(self, profile):
        # gpt-oss has no documented "none" effort, so don't risk a 400.
        extra_body, top_level = profile.build_api_kwargs_extras(
            model="gpt-oss-120b", reasoning_config={"enabled": False}
        )
        assert (extra_body, top_level) == ({}, {})


class TestCerebrasTransportIntegration:
    """End-to-end through the real ChatCompletions transport path Cerebras uses.

    Guards against the shims silently going dead if the transport ever stops
    calling ``prepare_messages``/``build_extra_body`` (the unit tests above would
    still pass in that case).
    """

    def _build(self, model, messages, params):
        from agent.transports.chat_completions import ChatCompletionsTransport

        # ``_build_kwargs_from_profile`` is stateless w.r.t. ``self``; bypass
        # __init__ (which wires an OpenAI client) and call it directly.
        transport = object.__new__(ChatCompletionsTransport)
        profile = get_provider_profile("cerebras")
        return transport._build_kwargs_from_profile(
            profile, model, messages, None, params
        )

    def _messages(self):
        # Shape after copy_reasoning_content_for_api has promoted internal
        # reasoning to reasoning_content for the OpenAI-compatible replay.
        return [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "ok",
                "reasoning_content": "prior thought",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "content": "result", "tool_call_id": "c1"},
        ]

    def test_glm_replay_moves_reasoning_and_sets_clear_thinking(self):
        kwargs = self._build(
            "zai-glm-4.7", self._messages(), {"reasoning_config": {"enabled": True}}
        )
        assistant = next(m for m in kwargs["messages"] if m.get("role") == "assistant")
        assert "reasoning" not in assistant
        assert "reasoning_content" not in assistant
        assert assistant["content"] == "<think>prior thought</think>ok"
        assert kwargs["extra_body"]["clear_thinking"] is False
        assert "reasoning_effort" not in kwargs

    def test_glm_default_path_still_strips_and_preserves_setting(self):
        # No reasoning_config in params (the common default) must behave the same.
        kwargs = self._build("zai-glm-4.7", self._messages(), {})
        assistant = next(m for m in kwargs["messages"] if m.get("role") == "assistant")
        assert "reasoning" not in assistant
        assert "reasoning_content" not in assistant
        assert assistant["content"] == "<think>prior thought</think>ok"
        assert kwargs["extra_body"]["clear_thinking"] is False

    def test_no_reasoning_content_ever_reaches_request(self):
        kwargs = self._build("zai-glm-4.7", self._messages(), {})
        assert all("reasoning_content" not in m for m in kwargs["messages"])
