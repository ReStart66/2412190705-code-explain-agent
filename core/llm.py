"""LLM 客户端（零第三方依赖）。

只用 Python 标准库实现，直接请求任意 OpenAI 兼容的
`POST {base_url}/chat/completions` 接口。特点：

- 不需要 pip 安装任何第三方包；
- 支持 tool calling（function calling），Agent 循环照常工作；
- 对超时 / 限流 / 网络错误做指数退避重试，4xx 直接抛出；
- transport（传输层）可注入，方便在离线环境下做单元测试。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Optional

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

# 传输层签名：(url, headers, payload, timeout) -> 解析后的 JSON(dict)
Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


class LLMError(RuntimeError):
    """LLM 调用相关的错误。"""


@dataclass
class LLMConfig:
    """LLM 连接配置。"""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = 0.2
    max_tokens: int = 2048
    max_retries: int = 3
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """从环境变量读取配置；缺少 Key 时抛出带提示的错误。"""
        api_key = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise LLMError(
                "未找到 API Key。请复制 .env.example 为 .env，并填写 LLM_API_KEY。"
            )
        return cls(
            api_key=api_key,
            base_url=(os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/"),
            model=(os.getenv("LLM_MODEL") or DEFAULT_MODEL).strip(),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2048")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            timeout=float(os.getenv("LLM_TIMEOUT", "60")),
        )


def _default_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """用标准库 urllib 发起一次 POST 请求。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def parse_message(data: dict[str, Any]) -> Any:
    """把接口返回的 JSON 转换成 Agent 需要的 message 对象。"""
    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"接口响应中没有 choices：{str(data)[:300]}")

    message = choices[0].get("message") or {}
    tool_calls = []
    for index, item in enumerate(message.get("tool_calls") or []):
        function = item.get("function") or {}
        tool_calls.append(
            SimpleNamespace(
                id=item.get("id") or f"call_{index}",
                type=item.get("type") or "function",
                function=SimpleNamespace(
                    name=function.get("name"),
                    arguments=function.get("arguments") or "{}",
                ),
            )
        )

    return SimpleNamespace(content=message.get("content"), tool_calls=tool_calls or None)


class LLMClient:
    """对 OpenAI 兼容 chat.completions 接口的轻量封装。"""

    def __init__(self, config: LLMConfig, transport: Optional[Transport] = None) -> None:
        self.config = config
        self._transport = transport or _default_transport

    @property
    def endpoint(self) -> str:
        return f"{self.config.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> Any:
        """调用一次模型，返回 message 对象（可能带有 tool_calls）。"""
        url = self.endpoint
        headers = self._headers()
        payload = self._payload(messages, tools)
        last_error: Optional[Exception] = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                data = self._transport(url, headers, payload, self.config.timeout)
                return parse_message(data)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    pass
                # 4xx（除 429 限流外）重试没有意义，直接抛出
                if 400 <= exc.code < 500 and exc.code != 429:
                    raise LLMError(f"接口返回 {exc.code}：{detail[:500]}") from exc
                last_error = LLMError(f"HTTP {exc.code}：{detail[:200]}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            except json.JSONDecodeError as exc:
                raise LLMError(f"接口返回的不是合法 JSON：{exc}") from exc

            if attempt < self.config.max_retries:
                wait = 2 ** (attempt - 1)
                print(f"[重试] 第 {attempt} 次调用失败：{last_error}；{wait}s 后重试…")
                time.sleep(wait)

        raise LLMError(f"LLM 调用失败（已重试 {self.config.max_retries} 次）：{last_error}")
