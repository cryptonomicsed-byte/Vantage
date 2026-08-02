"""Static registry of known LLM/AI providers for the API-key management
feature. Adding provider #21 (or #40) is a new dict entry here, never a DB
migration -- provider_credentials (db.py) stores everything generically
by provider_id.

`chat_compatible=True` means the provider's REST API is wire-compatible
with OpenAI's /v1/chat/completions request/response shape closely enough
that Vantage's generic caller (llm_provider_client.py) can use it directly
for Copilot chat. This is asserted per-provider from each provider's own
public docs, not assumed -- providers with their own distinct wire format
(Anthropic's Messages API, Google's generateContent, AWS's SigV4-signed
requests, Cohere's own chat shape) are marked False: their keys can be
safely stored and managed today, but actually calling them for chat would
need a dedicated adapter that does not exist yet. Being honest about this
split matters more than making the provider count look uniform.

`auth_header_style` covers the one real variation in how the key gets
attached: "bearer" (`Authorization: Bearer <key>`, the OpenAI-compatible
norm) or "api-key" (Azure OpenAI's `api-key: <key>` header).

Media/generation providers (TTS, image, video) are included for secure
storage even though none of them are chat_compatible -- they're not
wired to any Vantage call path yet, this just future-proofs the vault for
when they are.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    display_name: str
    base_url_default: Optional[str]
    model_default: Optional[str]
    chat_compatible: bool
    auth_header_style: str  # "bearer" | "api-key" | "none"
    docs_note: str = ""


PROVIDERS: dict[str, ProviderInfo] = {
    p.id: p
    for p in [
        ProviderInfo("omniroute", "OmniRoute (built-in)", "http://localhost:8300",
                     None, True, "none",
                     "Vantage's own local aggregator -- no key needed, this entry exists "
                     "so it appears alongside real providers in the same list."),
        ProviderInfo("openai", "OpenAI", "https://api.openai.com/v1",
                     "gpt-5.1", True, "bearer"),
        ProviderInfo("anthropic", "Anthropic", "https://api.anthropic.com/v1",
                     "claude-sonnet-5", False, "bearer",
                     "Messages API, not chat-completions-shaped -- stored, not yet wired for chat."),
        ProviderInfo("google", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta",
                     "gemini-3-pro", False, "bearer",
                     "generateContent API, not chat-completions-shaped -- stored, not yet wired."),
        ProviderInfo("meta", "Meta Model API", "https://api.llama.com/compat/v1",
                     "muse-spark-1.1", True, "bearer",
                     "OpenAI-compatible endpoint per ai.meta.com's public API docs."),
        ProviderInfo("mistral", "Mistral", "https://api.mistral.ai/v1",
                     "mistral-large-latest", True, "bearer"),
        ProviderInfo("cohere", "Cohere", "https://api.cohere.com/v1",
                     "command-a", False, "bearer",
                     "Own chat API shape -- stored, not yet wired for chat."),
        ProviderInfo("xai", "xAI (Grok)", "https://api.x.ai/v1",
                     "grok-4", True, "bearer"),
        ProviderInfo("deepseek", "DeepSeek", "https://api.deepseek.com/v1",
                     "deepseek-chat", True, "bearer"),
        ProviderInfo("groq", "Groq", "https://api.groq.com/openai/v1",
                     "llama-3.3-70b-versatile", True, "bearer"),
        ProviderInfo("together", "Together AI", "https://api.together.xyz/v1",
                     None, True, "bearer"),
        ProviderInfo("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1",
                     None, True, "bearer"),
        ProviderInfo("perplexity", "Perplexity", "https://api.perplexity.ai",
                     "sonar", True, "bearer"),
        ProviderInfo("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
                     None, True, "bearer"),
        ProviderInfo("azure_openai", "Azure OpenAI", None,
                     None, True, "api-key",
                     "base_url has no default -- supply the FULL completions URL for your "
                     "deployment, including api-version query string: "
                     "https://<resource>.openai.azure.com/openai/deployments/<deployment>/chat/completions?api-version=2026-01-01"),
        ProviderInfo("aws_bedrock", "AWS Bedrock", None,
                     None, False, "none",
                     "SigV4-signed requests, not a bearer-token REST API -- stored, not yet wired."),
        ProviderInfo("qwen", "Alibaba Qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                     "qwen-max", True, "bearer"),
        ProviderInfo("moonshot", "Moonshot / Kimi", "https://api.moonshot.ai/v1",
                     "kimi-k2", True, "bearer"),
        ProviderInfo("cerebras", "Cerebras", "https://api.cerebras.ai/v1",
                     None, True, "bearer"),
        ProviderInfo("huggingface", "HuggingFace Inference", "https://router.huggingface.co/v1",
                     None, True, "bearer"),
        ProviderInfo("replicate", "Replicate", "https://api.replicate.com/v1",
                     None, False, "bearer",
                     "Async prediction API, not chat-completions-shaped -- stored, not yet wired."),
        ProviderInfo("elevenlabs", "ElevenLabs (voice/TTS)", "https://api.elevenlabs.io/v1",
                     None, False, "none",
                     "TTS, not a chat provider -- stored for future use."),
        ProviderInfo("fal", "Fal.ai (image/video/avatar gen)", "https://fal.run",
                     None, False, "none",
                     "Media generation, not a chat provider -- stored for future use."),
        ProviderInfo("stability", "Stability AI (image)", "https://api.stability.ai/v2beta",
                     None, False, "none",
                     "Image generation, not a chat provider -- stored for future use."),
        ProviderInfo("runway", "Runway (video)", "https://api.dev.runwayml.com/v1",
                     None, False, "none",
                     "Video generation, not a chat provider -- stored for future use."),
    ]
}


def is_custom_provider_id(provider_id: str) -> bool:
    return provider_id.startswith("custom:")


def get_provider(provider_id: str) -> Optional[ProviderInfo]:
    """Known providers come from PROVIDERS; a `custom:<slug>` id is a
    user-defined entry with no registry row -- callers must have the
    user-supplied base_url/display_name/model on hand for those (stored
    per-credential in provider_credentials, not here)."""
    if is_custom_provider_id(provider_id):
        return None
    return PROVIDERS.get(provider_id)


def list_registry() -> list[dict]:
    """For the frontend's provider dropdown."""
    return [
        {
            "id": p.id,
            "display_name": p.display_name,
            "base_url_default": p.base_url_default,
            "model_default": p.model_default,
            "chat_compatible": p.chat_compatible,
            "auth_header_style": p.auth_header_style,
            "docs_note": p.docs_note,
        }
        for p in PROVIDERS.values()
    ]
