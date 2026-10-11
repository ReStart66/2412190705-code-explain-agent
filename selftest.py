"""离线自检：不联网、不需要 API Key，验证 Agent 循环、工具调用和 HTTP 层。

运行：
    python selftest.py

三部分：
1. Agent 循环   —— 用「脚本化假 LLM」跑通 推理 → 工具调用 → 再推理 → 输出
2. 边界情况     —— 路径越界、文件不存在、非法参数、未知工具
3. LLM 传输层   —— 用「假 transport」验证请求构造、响应解析、重试与报错
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

from core.agent import CodeExplainAgent
from core.llm import LLMClient, LLMConfig, LLMError, parse_message
from core.tools import ToolRegistry, Workspace, make_tools

# Windows 控制台默认可能是 GBK，这里强制 UTF-8，避免中文/符号乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass


class ScriptedClient:
    """按脚本依次返回预设回复的假 LLM 客户端。"""

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)
        self.calls = 0

    def chat(self, messages, tools=None):  # noqa: ANN001, ANN201 - 模拟 LLM 接口
        self.calls += 1
        item = self._script.pop(0)
        if item.get("tool_call"):
            name, args = item["tool_call"]
            tc = SimpleNamespace(
                id=f"call_{self.calls}",
                type="function",
                function=SimpleNamespace(name=name, arguments=args),
            )
            return SimpleNamespace(content=None, tool_calls=[tc])
        return SimpleNamespace(content=item.get("content", ""), tool_calls=None)


def check(label: str, condition: bool) -> bool:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}")
    return condition


def check_agent_layer() -> bool:
    """第 1、2 部分：Agent 循环 + 边界情况。"""
    base = Path(__file__).resolve().parent
    workspace = Workspace(base / "examples")
    tools = ToolRegistry(make_tools(workspace))

    client = ScriptedClient(
        [
            {"tool_call": ("read_file", '{"path": "sample_code.py"}')},
            {"content": "这个文件封装了一个带重试的 HTTP 请求工具。"},
        ]
    )
    agent = CodeExplainAgent(client=client, tools=tools, verbose=False)
    answer = agent.run("解释 sample_code.py")

    ok = True
    ok &= check("Agent 调用了两次 LLM（一次决策、一次总结）", client.calls == 2)
    ok &= check("最终答案已返回", "HTTP" in answer)

    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    ok &= check(
        "工具结果已回填到上下文",
        len(tool_msgs) == 1 and "fetch_data" in tool_msgs[0]["content"],
    )
    assistant_with_tools = [
        m for m in agent.messages if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    ok &= check(
        "assistant 消息保留了 tool_calls 协议字段",
        len(assistant_with_tools) == 1
        and assistant_with_tools[0]["tool_calls"][0]["function"]["name"] == "read_file",
    )
    ok &= check("上下文包含 system + user + 两轮 assistant", len(agent.messages) == 5)

    bad = tools.call("read_file", '{"path": "../../../etc/passwd"}')
    ok &= check("路径越界被拦截", bad.startswith("[错误]"))
    missing = tools.call("read_file", '{"path": "not_exist.py"}')
    ok &= check("文件不存在被友好处理", "不存在" in missing)
    invalid = tools.call("read_file", "{not json}")
    ok &= check("非法 JSON 参数被友好处理", invalid.startswith("[错误]"))
    unknown = tools.call("no_such_tool", "{}")
    ok &= check("未知工具被友好处理", "未知工具" in unknown)

    return ok


def check_llm_layer() -> bool:
    """第 3 部分：HTTP 传输层（用假 transport，不联网）。"""
    captured: dict = {}

    def fake_transport(url, headers, payload, timeout):
        captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return {"choices": [{"message": {"content": "你好，这是解释。"}}]}

    config = LLMConfig(api_key="sk-test", base_url="https://example.com/v1", model="test-model")
    client = LLMClient(config, transport=fake_transport)
    message = client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    ok = True
    ok &= check("请求发往 /chat/completions", captured["url"] == "https://example.com/v1/chat/completions")
    ok &= check(
        "请求头带 Bearer Token",
        captured["headers"]["Authorization"] == "Bearer sk-test",
    )
    ok &= check("请求体包含 model 与 messages", captured["payload"]["model"] == "test-model")
    ok &= check("传入 tools 时会带上 tools 字段", "tools" in captured["payload"])
    ok &= check("正常响应解析出 content", message.content == "你好，这是解释。")

    # 解析 tool_calls
    parsed = parse_message(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
                            }
                        ],
                    }
                }
            ]
        }
    )
    ok &= check(
        "tool_calls 被正确解析",
        parsed.tool_calls[0].function.name == "read_file"
        and parsed.tool_calls[0].id == "call_1",
    )

    # 401 应直接抛错且不重试
    attempts = {"n": 0}

    def unauthorized(url, headers, payload, timeout):
        attempts["n"] += 1
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))

    try:
        LLMClient(LLMConfig(api_key="bad", max_retries=3), transport=unauthorized).chat(
            [{"role": "user", "content": "hi"}]
        )
        ok &= check("401 抛出 LLMError", False)
    except LLMError:
        ok &= check("401 抛出 LLMError", True)
    ok &= check("401 不重试（只调用一次）", attempts["n"] == 1)

    # 503 应重试
    attempts2 = {"n": 0}

    def unavailable(url, headers, payload, timeout):
        attempts2["n"] += 1
        if attempts2["n"] < 3:
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, io.BytesIO(b"oops"))
        return {"choices": [{"message": {"content": "第三次成功"}}]}

    import core.llm as llm_module

    original_sleep = llm_module.time.sleep
    llm_module.time.sleep = lambda _seconds: None  # 跳过重试等待，加快自检
    try:
        result = LLMClient(LLMConfig(api_key="x", max_retries=3), transport=unavailable).chat(
            [{"role": "user", "content": "hi"}]
        )
    finally:
        llm_module.time.sleep = original_sleep

    ok &= check("503 自动重试后成功", result.content == "第三次成功" and attempts2["n"] == 3)

    # 缺少 choices 的畸形响应
    try:
        parse_message({})
        ok &= check("畸形响应抛出 LLMError", False)
    except LLMError:
        ok &= check("畸形响应抛出 LLMError", True)

    return ok


def main() -> int:
    print("== 离线自检 ==")
    print("-- Agent 循环与边界情况 --")
    ok1 = check_agent_layer()
    print("\n-- LLM 传输层（假 transport） --")
    ok2 = check_llm_layer()

    ok = ok1 and ok2
    print()
    print("结果：" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
