"""Code Explain Agent 的核心：一个最小的 Agent 循环。

循环流程（对应作业要求里的「输入 → 推理 → 工具调用 → 输出」）：

    用户输入
      ↓
    调用 LLM（推理：要不要用工具、用哪个）
      ↓
    若返回 tool_calls → 执行工具 → 把结果塞回对话 → 回到上一步
      ↓
    若不再调用工具 → 直接输出最终答案
"""

from __future__ import annotations

from typing import Any, Optional

from core.prompts import SYSTEM_PROMPT
from core.tools import ToolRegistry


class CodeExplainAgent:
    """代码解释 Agent。

    参数：
        client: LLM 客户端，只需实现 chat(messages, tools) -> message 即可，
                因此可以很方便地换成其它厂商，或注入假客户端做离线测试。
        tools:  工具注册表（ToolRegistry）。
        system_prompt: 系统提示词。
        max_steps: 单轮对话里最多允许的「推理 + 工具调用」轮数，防止死循环。
    """

    def __init__(
        self,
        client: Any,
        tools: ToolRegistry,
        system_prompt: str = SYSTEM_PROMPT,
        max_steps: int = 6,
        verbose: bool = True,
    ) -> None:
        self.client = client
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.verbose = verbose
        # messages 就是「上下文记忆」：同一会话内的历史会被一直带上
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    def clear(self) -> None:
        """清空上下文，但保留系统提示词。"""
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def _log(self, text: str) -> None:
        if self.verbose:
            print(text)

    def run(self, user_input: str) -> str:
        """处理一次用户输入，返回最终回答文本。"""
        user_input = (user_input or "").strip()
        if not user_input:
            return "请输入内容，例如：解释 examples/sample_code.py"

        self.messages.append({"role": "user", "content": user_input})

        for step in range(1, self.max_steps + 1):
            message = self.client.chat(self.messages, tools=self.tools.schemas())
            tool_calls = getattr(message, "tool_calls", None)
            content: Optional[str] = getattr(message, "content", None)

            # 把模型的回复（含工具调用意图）记入上下文，保持工具调用协议完整
            assistant_record: dict[str, Any] = {"role": "assistant", "content": content or ""}
            if tool_calls:
                assistant_record["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ]
            self.messages.append(assistant_record)

            # 没有工具调用 → 这就是最终答案
            if not tool_calls:
                return content or ""

            # 有工具调用 → 依次执行，把结果回填给模型
            for tc in tool_calls:
                name = tc.function.name
                arguments = tc.function.arguments or "{}"
                self._log(f"  [第 {step} 步] 调用工具 {name}({arguments})")
                result = self.tools.call(name, arguments)
                head = result.splitlines()[0] if result else ""
                self._log(f"           ↳ {head}")
                self.messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )

        return "（已达最大推理步数，未能得出结论。请简化问题或重试。）"
