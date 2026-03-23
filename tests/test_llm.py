"""Tests for unified LLM backend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bugeval.llm import _DEFAULT_MODELS, BACKENDS, LLMResult, call_llm


def _mock_result(text: str = "ok", backend: str = "api") -> LLMResult:
    return LLMResult(text=text, backend=backend, model="test")


class TestCallLlmDispatch:
    @patch("bugeval.llm._call_api", return_value=_mock_result("api-response", "api"))
    def test_dispatches_to_api(self, mock: MagicMock) -> None:
        result = call_llm("hello", backend="api")
        assert result.text == "api-response"
        mock.assert_called_once()

    @patch("bugeval.llm._call_sdk", return_value=_mock_result("sdk-response", "sdk"))
    def test_dispatches_to_sdk(self, mock: MagicMock) -> None:
        result = call_llm("hello", backend="sdk")
        assert result.text == "sdk-response"
        mock.assert_called_once()

    @patch("bugeval.llm._call_gemini", return_value=_mock_result("gemini-response", "gemini"))
    def test_dispatches_to_gemini(self, mock: MagicMock) -> None:
        result = call_llm("hello", backend="gemini")
        assert result.text == "gemini-response"
        mock.assert_called_once()

    @patch("bugeval.llm._call_openai", return_value=_mock_result("openai-response", "openai"))
    def test_dispatches_to_openai(self, mock: MagicMock) -> None:
        result = call_llm("hello", backend="openai")
        assert result.text == "openai-response"
        mock.assert_called_once()


class TestCallLlmErrorHandling:
    @patch("bugeval.llm._call_api", side_effect=RuntimeError("auth failed"))
    def test_returns_error_result_on_exception(self, mock: MagicMock) -> None:
        result = call_llm("hello", backend="api")
        assert result.error == "auth failed"
        assert result.text == ""


class TestUnknownBackend:
    def test_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown backend"):
            call_llm("hello", backend="invalid")


class TestDefaultModelResolution:
    @patch("bugeval.llm._call_api", return_value=_mock_result())
    def test_default_model_api(self, mock: MagicMock) -> None:
        call_llm("hi", backend="api")
        args = mock.call_args[0]
        assert args[1] == _DEFAULT_MODELS["api"]

    @patch("bugeval.llm._call_sdk", return_value=_mock_result())
    def test_custom_model_overrides_default(self, mock: MagicMock) -> None:
        call_llm("hi", model="claude-opus-4-6", backend="sdk")
        args = mock.call_args[0]
        assert args[1] == "claude-opus-4-6"

    def test_all_backends_have_defaults(self) -> None:
        for b in BACKENDS:
            assert b in _DEFAULT_MODELS


class TestLLMResult:
    def test_defaults(self) -> None:
        r = LLMResult()
        assert r.text == ""
        assert r.cost_usd == 0.0
        assert r.error == ""
        assert r.prompt == ""

    def test_cost_tracking(self) -> None:
        r = LLMResult(text="hello", cost_usd=0.001, input_tokens=100, output_tokens=50)
        assert r.cost_usd == 0.001
        assert r.input_tokens == 100


class TestMaxTokensConfigurable:
    @patch("bugeval.llm._call_api", return_value=_mock_result())
    def test_max_tokens_passed_to_api(self, mock: MagicMock) -> None:
        call_llm("hi", backend="api", max_tokens=4096)
        args = mock.call_args[0]
        assert args[2] == 4096  # third positional arg is max_tokens
