"""
Unit tests for src/doc_exchange/planner/llm_client.py

覆盖：
- make_llm_client 工厂函数（env 优先级、provider 路由、api_key 缺失返回 None）
- OpenAIClient / AnthropicClient 依赖缺失时的 ImportError
- 非流式与流式补全（通过 mock，不依赖外部网络）
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_exchange.planner.llm_client import (
    AnthropicClient,
    LLMClient,
    OpenAIClient,
    make_llm_client,
)


# ---------------------------------------------------------------------------
# make_llm_client — 工厂函数
# ---------------------------------------------------------------------------

class TestMakeLlmClient:
    def test_returns_none_when_no_api_key(self, monkeypatch):
        """无 api_key（参数和 env 都为空）时应返回 None（优雅降级）。"""
        monkeypatch.delenv("PLANNER_LLM_API_KEY", raising=False)
        monkeypatch.delenv("PLANNER_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("PLANNER_LLM_MODEL", raising=False)
        result = make_llm_client()
        assert result is None

    def test_returns_none_when_api_key_empty_string(self, monkeypatch):
        """env 中 api_key 为空字符串时也应返回 None。"""
        monkeypatch.setenv("PLANNER_LLM_API_KEY", "")
        result = make_llm_client()
        assert result is None

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        """显式传入 api_key 优先于 env。"""
        monkeypatch.delenv("PLANNER_LLM_API_KEY", raising=False)
        # OpenAI 的 __init__ 会 import openai，需要 mock
        with patch("doc_exchange.planner.llm_client.OpenAIClient.__init__", return_value=None):
            client = make_llm_client(provider="openai", api_key="sk-explicit")
        assert client is not None

    def test_env_api_key_used_when_no_explicit(self, monkeypatch):
        """无显式 api_key 时从 env 读取。"""
        monkeypatch.setenv("PLANNER_LLM_API_KEY", "sk-from-env")
        monkeypatch.setenv("PLANNER_LLM_PROVIDER", "openai")
        with patch("doc_exchange.planner.llm_client.OpenAIClient.__init__", return_value=None):
            client = make_llm_client()
        assert client is not None

    def test_routes_to_openai_client(self, monkeypatch):
        """provider=openai 时返回 OpenAIClient 实例。"""
        monkeypatch.setenv("PLANNER_LLM_API_KEY", "sk-test")
        with patch("doc_exchange.planner.llm_client.OpenAIClient.__init__", return_value=None):
            client = make_llm_client(provider="openai", api_key="sk-test")
        assert isinstance(client, OpenAIClient)

    def test_routes_to_anthropic_client(self, monkeypatch):
        """provider=anthropic 时返回 AnthropicClient 实例。"""
        with patch("doc_exchange.planner.llm_client.AnthropicClient.__init__", return_value=None):
            client = make_llm_client(provider="anthropic", api_key="sk-ant-test")
        assert isinstance(client, AnthropicClient)

    def test_alias_gpt_routes_to_openai(self, monkeypatch):
        """'gpt' 别名应路由到 OpenAIClient。"""
        with patch("doc_exchange.planner.llm_client.OpenAIClient.__init__", return_value=None):
            client = make_llm_client(provider="gpt", api_key="sk-test")
        assert isinstance(client, OpenAIClient)

    def test_alias_claude_routes_to_anthropic(self, monkeypatch):
        """'claude' 别名应路由到 AnthropicClient。"""
        with patch("doc_exchange.planner.llm_client.AnthropicClient.__init__", return_value=None):
            client = make_llm_client(provider="claude", api_key="sk-ant-test")
        assert isinstance(client, AnthropicClient)

    def test_unknown_provider_raises_value_error(self):
        """未知 provider 应抛出 ValueError，不是静默失败。"""
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            make_llm_client(provider="unknown_llm", api_key="sk-test")

    def test_model_passed_to_openai_client(self):
        """model 参数应传递给 OpenAIClient。"""
        with patch.object(OpenAIClient, "__init__", return_value=None) as mock_init:
            make_llm_client(provider="openai", model="gpt-4-turbo", api_key="sk-test")
        mock_init.assert_called_once_with(model="gpt-4-turbo", api_key="sk-test")

    def test_model_passed_to_anthropic_client(self):
        """model 参数应传递给 AnthropicClient。"""
        with patch.object(AnthropicClient, "__init__", return_value=None) as mock_init:
            make_llm_client(provider="anthropic", model="claude-3-opus-20240229", api_key="sk-ant-test")
        mock_init.assert_called_once_with(model="claude-3-opus-20240229", api_key="sk-ant-test")

    def test_env_model_used_when_no_explicit(self, monkeypatch):
        """无显式 model 时从 PLANNER_LLM_MODEL env 读取。"""
        monkeypatch.setenv("PLANNER_LLM_MODEL", "gpt-4-env")
        monkeypatch.setenv("PLANNER_LLM_PROVIDER", "openai")
        with patch.object(OpenAIClient, "__init__", return_value=None) as mock_init:
            make_llm_client(api_key="sk-test")
        mock_init.assert_called_once_with(model="gpt-4-env", api_key="sk-test")


# ---------------------------------------------------------------------------
# LLMClient — 抽象基类约束
# ---------------------------------------------------------------------------

class TestLLMClientAbstract:
    def test_cannot_instantiate_abstract_class(self):
        """LLMClient 是 ABC，不能直接实例化。"""
        with pytest.raises(TypeError):
            LLMClient()  # type: ignore[abstract]

    def test_subclass_must_implement_complete(self):
        """子类若不实现 complete，应无法实例化。"""
        class IncompleteClient(LLMClient):
            pass  # 未实现 complete

        with pytest.raises(TypeError):
            IncompleteClient()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# OpenAIClient
# ---------------------------------------------------------------------------

class TestOpenAIClient:
    def test_raises_import_error_when_openai_not_installed(self):
        """openai 包不存在时，实例化应抛出带有安装提示的 ImportError。"""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="pip install openai"):
                OpenAIClient(api_key="sk-test")

    @pytest.mark.asyncio
    async def test_complete_non_stream_returns_string(self):
        """非流式 complete 应返回字符串。"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello from OpenAI"

        mock_openai = MagicMock()
        mock_openai.AsyncOpenAI.return_value.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        with patch.dict("sys.modules", {"openai": mock_openai}):
            client = OpenAIClient(model="gpt-4o", api_key="sk-test")
            result = await client.complete("sys", "user", stream=False)

        assert result == "Hello from OpenAI"

    @pytest.mark.asyncio
    async def test_complete_stream_returns_async_iterator(self):
        """流式 complete 应返回异步迭代器，逐块 yield 内容。"""
        mock_openai = MagicMock()
        mock_openai.AsyncOpenAI.return_value.chat.completions.create = AsyncMock(
            return_value=MagicMock()
        )

        with patch.dict("sys.modules", {"openai": mock_openai}):
            client = OpenAIClient(model="gpt-4o", api_key="sk-test")
            result = await client.complete("sys", "user", stream=True)

        # 流式时应返回异步迭代器（生成器对象）
        import inspect
        assert inspect.isasyncgen(result) or hasattr(result, "__aiter__")


# ---------------------------------------------------------------------------
# AnthropicClient
# ---------------------------------------------------------------------------

class TestAnthropicClient:
    def test_raises_import_error_when_anthropic_not_installed(self):
        """anthropic 包不存在时，实例化应抛出带有安装提示的 ImportError。"""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="pip install anthropic"):
                AnthropicClient(api_key="sk-ant-test")

    @pytest.mark.asyncio
    async def test_complete_non_stream_returns_string(self):
        """非流式 complete 应返回字符串（取第一个 text block）。"""
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello from Anthropic"

        mock_response = MagicMock()
        mock_response.content = [text_block]

        mock_anthropic_module = MagicMock()
        mock_anthropic_module.AsyncAnthropic.return_value.messages.create = AsyncMock(
            return_value=mock_response
        )

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            client = AnthropicClient(api_key="sk-ant-test")
            result = await client.complete("sys", "user", stream=False)

        assert result == "Hello from Anthropic"

    def test_default_model_is_set(self):
        """未传 model 时使用 DEFAULT_MODEL。"""
        mock_anthropic_module = MagicMock()
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            client = AnthropicClient(api_key="sk-ant-test")
        assert client._model == AnthropicClient.DEFAULT_MODEL

    def test_custom_model_is_respected(self):
        """显式传 model 应被使用。"""
        mock_anthropic_module = MagicMock()
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            client = AnthropicClient(model="claude-3-opus-20240229", api_key="sk-ant-test")
        assert client._model == "claude-3-opus-20240229"
