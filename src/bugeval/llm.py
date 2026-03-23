"""Unified single-turn LLM call layer.

All components that need LLM calls (judge, validate) go through this module.
Multi-turn tool-use runners in agent_runner.py remain separate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

BACKENDS = ("api", "sdk", "gemini", "openai")

_DEFAULT_MODELS = {
    "api": "claude-haiku-4-5",
    "sdk": "claude-haiku-4-5",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4.1-mini",
}


@dataclass
class LLMResult:
    """Result from a single-turn LLM call."""

    text: str = ""
    cost_usd: float = 0.0
    model: str = ""
    backend: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    prompt: str = field(default="", repr=False)  # Full prompt for transcript


def call_llm(
    prompt: str,
    model: str = "",
    backend: str = "sdk",
    max_tokens: int = 2048,
) -> LLMResult:
    """Make a single-turn LLM call. Returns LLMResult with text, cost, and metadata.

    Backends:
      api    -- Anthropic API (needs ANTHROPIC_API_KEY)
      sdk    -- Claude Agent SDK (uses Claude Code auth, no key)
      gemini -- Google Gemini API (needs GEMINI_API_KEY)
      openai -- OpenAI API (needs OPENAI_API_KEY)
    """
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend: {backend}. Choose from {BACKENDS}")
    effective_model = model or _DEFAULT_MODELS[backend]

    try:
        if backend == "api":
            return _call_api(prompt, effective_model, max_tokens)
        elif backend == "sdk":
            return _call_sdk(prompt, effective_model)
        elif backend == "gemini":
            return _call_gemini(prompt, effective_model, max_tokens)
        elif backend == "openai":
            return _call_openai(prompt, effective_model, max_tokens)
    except Exception as exc:
        log.warning("LLM call failed (backend=%s, model=%s): %s", backend, effective_model, exc)
        return LLMResult(
            error=str(exc),
            model=effective_model,
            backend=backend,
            prompt=prompt,
        )
    raise ValueError(f"Unknown backend: {backend}")  # pragma: no cover


def _call_api(prompt: str, model: str, max_tokens: int) -> LLMResult:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text  # type: ignore[union-attr]
    usage = response.usage
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    # Haiku pricing: $0.80/$4.00 per MTok
    cost = inp * 0.80 / 1e6 + out * 4.00 / 1e6
    return LLMResult(
        text=text,
        cost_usd=round(cost, 6),
        model=model,
        backend="api",
        input_tokens=inp,
        output_tokens=out,
        prompt=prompt,
    )


def _call_sdk(prompt: str, model: str) -> LLMResult:
    import asyncio

    from claude_agent_sdk import (  # type: ignore[import-untyped]
        ClaudeAgentOptions,
        ResultMessage,
        query,
    )

    async def _run() -> LLMResult:
        options = ClaudeAgentOptions(
            model=model,
            max_turns=1,
            permission_mode="acceptEdits",
            env={"CLAUDECODE": ""},
        )
        result_text = ""
        cost = 0.0
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                result_text = message.result or ""
                cost = message.total_cost_usd or 0.0
        return LLMResult(
            text=result_text,
            cost_usd=round(cost, 6),
            model=model,
            backend="sdk",
            prompt=prompt,
        )

    return asyncio.run(_run())


def _call_gemini(prompt: str, model: str, max_tokens: int) -> LLMResult:
    from google import genai  # type: ignore[import-untyped]
    from google.genai import types as genai_types  # type: ignore[import-untyped]

    client = genai.Client()
    config = genai_types.GenerateContentConfig(max_output_tokens=max_tokens)
    response = client.models.generate_content(
        model=model,
        contents=[prompt],
        config=config,
    )
    text = response.text or ""  # type: ignore[union-attr]
    usage = getattr(response, "usage_metadata", None)
    inp = getattr(usage, "prompt_token_count", 0) or 0 if usage else 0
    out = getattr(usage, "candidates_token_count", 0) or 0 if usage else 0
    # Flash pricing: $0.15/$0.60 per MTok
    cost = inp * 0.15 / 1e6 + out * 0.60 / 1e6
    return LLMResult(
        text=text,
        cost_usd=round(cost, 6),
        model=model,
        backend="gemini",
        input_tokens=inp,
        output_tokens=out,
        prompt=prompt,
    )


def _call_openai(prompt: str, model: str, max_tokens: int) -> LLMResult:
    import openai

    client = openai.OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=max_tokens,
        temperature=0,
    )
    text = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)
    inp = getattr(usage, "prompt_tokens", 0) or 0 if usage else 0
    out = getattr(usage, "completion_tokens", 0) or 0 if usage else 0
    # o4-mini pricing: $1.10/$4.40 per MTok
    cost = inp * 1.10 / 1e6 + out * 4.40 / 1e6
    return LLMResult(
        text=text,
        cost_usd=round(cost, 6),
        model=model,
        backend="openai",
        input_tokens=inp,
        output_tokens=out,
        prompt=prompt,
    )
