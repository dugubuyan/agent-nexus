"""
LLM 适配层：LLMClient 抽象 + provider 实现 + 工厂函数。

设计原则：
- 可插拔：通过抽象基类支持多 provider
- 可选依赖：openai/anthropic 不在 pyproject.toml 中，通过 try/import 动态加载
- 优雅降级：无 api_key 时 make_llm_client 返回 None，不崩溃
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import AsyncIterator


class LLMClient(ABC):
    """LLM 客户端抽象基类。

    所有 provider 实现此接口，PlannerService 通过该抽象调用 LLM，
    不直接依赖任何具体库。
    """

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        stream: bool = False,
    ) -> "str | AsyncIterator[str]":
        """调用 LLM 补全。

        Args:
            system_prompt: system 角色内容（含 injection 防护声明）
            user_prompt: user 角色内容（实际问题/需求）
            stream: 是否流式返回；True 时返回 AsyncIterator[str]，False 时返回 str

        Returns:
            非流式：str；流式：AsyncIterator[str]
        """
        ...


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

class OpenAIClient(LLMClient):
    """基于 openai SDK 的 LLM 客户端。

    需要安装 `openai` 包（`pip install openai`）。
    如果包未安装，实例化时会抛出明确的 ImportError，而不是在模块导入时崩溃。
    """

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None):
        try:
            from openai import AsyncOpenAI  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "OpenAI provider requires the 'openai' package. "
                "Install it with: pip install openai"
            ) from exc

        self._model = model
        self._client = AsyncOpenAI(api_key=api_key)

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        stream: bool = False,
    ) -> "str | AsyncIterator[str]":
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if stream:
            return self._stream_openai(messages)
        else:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=False,
            )
            return response.choices[0].message.content or ""

    async def _stream_openai(self, messages: list[dict]) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------

class AnthropicClient(LLMClient):
    """基于 anthropic SDK 的 LLM 客户端。

    需要安装 `anthropic` 包（`pip install anthropic`）。
    如果包未安装，实例化时会抛出明确的 ImportError，而不是在模块导入时崩溃。
    """

    DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
    MAX_TOKENS = 8192

    def __init__(self, model: str | None = None, api_key: str | None = None):
        try:
            import anthropic as _anthropic  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "Anthropic provider requires the 'anthropic' package. "
                "Install it with: pip install anthropic"
            ) from exc

        self._model = model or self.DEFAULT_MODEL
        self._client = _anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        stream: bool = False,
    ) -> "str | AsyncIterator[str]":
        if stream:
            return self._stream_anthropic(system_prompt, user_prompt)
        else:
            import anthropic as _anthropic  # noqa: PLC0415

            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self.MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            # 取第一个 text block 的内容
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""

    async def _stream_anthropic(
        self, system_prompt: str, user_prompt: str
    ) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self.MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            async for text in stream.text_stream:
                yield text


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

_PROVIDER_ALIASES: dict[str, str] = {
    "openai": "openai",
    "gpt": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
}


def make_llm_client(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> LLMClient | None:
    """工厂：按 env / 参数选择 provider，构造对应 LLMClient。

    优先级（高到低）：
        1. 函数参数（显式传入）
        2. 环境变量（PLANNER_LLM_PROVIDER / PLANNER_LLM_MODEL / PLANNER_LLM_API_KEY）

    无 api_key 时返回 None（优雅降级），调用方（PlannerService）负责
    在 AI 能力入口处返回 LLM_NOT_CONFIGURED 错误，读/写能力不受影响。

    Args:
        provider: provider 名称（openai | anthropic | ...），None 时读 env
        model: 模型名称，None 时读 env
        api_key: API 密钥，None 时读 env

    Returns:
        LLMClient 实例，或 None（未配置 api_key）
    """
    resolved_provider = provider or os.environ.get("PLANNER_LLM_PROVIDER", "openai")
    resolved_model = model or os.environ.get("PLANNER_LLM_MODEL") or None
    resolved_api_key = api_key or os.environ.get("PLANNER_LLM_API_KEY") or None

    # 无 api_key → 优雅降级
    if not resolved_api_key:
        return None

    # 规范化 provider 名称
    normalized = _PROVIDER_ALIASES.get(resolved_provider.lower(), resolved_provider.lower())

    if normalized == "openai":
        return OpenAIClient(
            model=resolved_model or "gpt-4o",
            api_key=resolved_api_key,
        )
    elif normalized == "anthropic":
        return AnthropicClient(
            model=resolved_model,
            api_key=resolved_api_key,
        )
    else:
        raise ValueError(
            f"Unknown LLM provider: {resolved_provider!r}. "
            "Supported providers: openai, anthropic"
        )
