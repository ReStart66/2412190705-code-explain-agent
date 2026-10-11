"""示例代码：供 Agent 解释/演示使用。

这是一个带「重试 + 超时」的简单 HTTP 请求封装，
其中包含循环、异常处理、默认参数等常见的讲解点。
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 5
DEFAULT_RETRIES = 3


def fetch_data(url: str, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> str:
    """请求一个 URL 并返回正文；失败时最多重试 retries 次。

    参数:
        url:     目标地址
        timeout: 单次请求超时秒数
        retries: 重试次数（不含首次请求）

    返回:
        响应正文（字符串）

    异常:
        RuntimeError: 全部尝试都失败时抛出
    """
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                wait = 2 ** attempt
                time.sleep(wait)

    raise RuntimeError(f"请求 {url} 失败，已重试 {retries} 次：{last_error}")


def summarize(text: str, max_length: int = 80) -> str:
    """把文本截断到 max_length 个字符，并在末尾加省略号。"""
    text = text.strip()
    if len(text) <= max_length:
        return text
    return text[:max_length] + "…"


if __name__ == "__main__":
    try:
        content = fetch_data("https://example.com")
        print(summarize(content))
    except RuntimeError as err:
        print(f"出错了：{err}")
