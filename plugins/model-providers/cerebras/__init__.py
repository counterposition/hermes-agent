"""Cerebras Inference provider profile."""

from providers import register_provider
from providers.base import ProviderProfile

cerebras = ProviderProfile(
    name="cerebras",
    env_vars=("CEREBRAS_API_KEY", "CEREBRAS_BASE_URL"),
    display_name="Cerebras",
    description="Cerebras — ultra-fast wafer-scale inference (OpenAI-compatible)",
    signup_url="https://cloud.cerebras.ai/",
    base_url="https://api.cerebras.ai/v1",
    auth_type="api_key",
    default_aux_model="llama-3.3-70b",
    fallback_models=(
        "llama-3.3-70b",
        "gpt-oss-120b",
    ),
)

register_provider(cerebras)
