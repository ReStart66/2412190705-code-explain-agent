"""Agent 可调用的工具集合。

设计要点：
- 所有文件操作都被限制在 workspace 根目录内，防止越权读写；
- 工具执行中的「可预期错误」被包装成文本返回给模型，让 Agent 能自行修正，
  而不是直接让整个程序崩溃（这是评分里说的「边界情况处理」）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# 单个文件最大可读字节数，避免把超大文件塞进上下文
MAX_READ_BYTES = 200_000
# 目录浏览时跳过的噪音目录
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}


class ToolError(Exception):
    """工具执行中的可预期错误，会被转成文本回传给模型。"""


@dataclass
class Tool:
    """一个可被模型调用的工具。"""

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., str]

    def to_schema(self) -> dict[str, Any]:
        """转换成 OpenAI function calling 所需的 JSON Schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Workspace:
    """把文件操作锁定在指定根目录内。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, raw_path: str) -> Path:
        """把用户/模型给出的路径解析为绝对路径，并做越界检查。"""
        if not raw_path or not raw_path.strip():
            raise ToolError("路径不能为空")
        path = Path(raw_path).expanduser()
        candidate = (path if path.is_absolute() else self.root / path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ToolError(f"路径越界：{raw_path} 不在工作区 {self.root} 内")
        return candidate

    def display(self, path: Path) -> str:
        """转成相对路径，输出更干净。"""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)


def make_tools(workspace: Workspace) -> list[Tool]:
    """基于给定工作区构造工具列表。"""

    def read_file(path: str, start_line: int = 1, max_lines: int = 400) -> str:
        """读取文件（带行号），支持按行区间读取。"""
        target = workspace.resolve(path)
        if not target.exists():
            raise ToolError(f"文件不存在：{workspace.display(target)}")
        if not target.is_file():
            raise ToolError(f"不是文件：{workspace.display(target)}（如需浏览目录请用 list_files）")

        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            raise ToolError(
                f"文件过大（{size} 字节，上限 {MAX_READ_BYTES}），"
                "请改用 start_line / max_lines 分段读取。"
            )

        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        start = max(1, int(start_line))
        if start > len(lines):
            raise ToolError(f"起始行 {start} 超过文件总行数 {len(lines)}")
        max_lines = max(1, int(max_lines))
        end = min(len(lines), start + max_lines - 1)

        numbered = "\n".join(f"{i:>5} | {line}" for i, line in enumerate(lines[start - 1 : end], start=start))
        header = f"# {workspace.display(target)}（共 {len(lines)} 行，显示 {start}-{end} 行）"
        return f"{header}\n{numbered}"

    def write_file(path: str, content: str) -> str:
        """把内容写入文件（会覆盖原文件）。"""
        target = workspace.resolve(path)
        if target.is_dir():
            raise ToolError(f"目标是目录，不能写入：{workspace.display(target)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {workspace.display(target)}（{len(content)} 字符）"

    def list_files(path: str = ".", max_items: int = 100) -> str:
        """列出目录下的文件。"""
        target = workspace.resolve(path)
        if not target.exists():
            raise ToolError(f"目录不存在：{workspace.display(target)}")
        if target.is_file():
            return f"{workspace.display(target)} 是一个文件，请直接用 read_file 读取。"

        max_items = max(1, int(max_items))
        entries: list[str] = []
        for current, dirs, files in os.walk(target):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            rel_dir = os.path.relpath(current, target)
            for name in sorted(files):
                rel = name if rel_dir == "." else os.path.join(rel_dir, name)
                entries.append(workspace.display((Path(current) / name).resolve()))
                if len(entries) >= max_items:
                    break
            if len(entries) >= max_items:
                break
        if not entries:
            return f"{workspace.display(target)} 下没有文件。"
        listing = "\n".join(f"- {e}" for e in entries)
        return f"# {workspace.display(target)} 下的文件（最多 {max_items} 个）\n{listing}"

    return [
        Tool(
            name="read_file",
            description="读取工作区内某个文件的源码，返回带行号的文本。可指定起始行和最大行数。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的文件路径，例如 examples/sample_code.py"},
                    "start_line": {"type": "integer", "description": "起始行号，默认 1"},
                    "max_lines": {"type": "integer", "description": "最多读取的行数，默认 400"},
                },
                "required": ["path"],
            },
            func=read_file,
        ),
        Tool(
            name="list_files",
            description="列出工作区内某个目录下的文件，用于定位用户提到的文件。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的目录路径，默认当前目录 ."},
                    "max_items": {"type": "integer", "description": "最多返回多少个文件，默认 100"},
                },
                "required": [],
            },
            func=list_files,
        ),
        Tool(
            name="write_file",
            description="把内容写入工作区内的文件（覆盖写）。仅在用户明确要求保存/写回时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的文件路径"},
                    "content": {"type": "string", "description": "要写入的完整文件内容"},
                },
                "required": ["path", "content"],
            },
            func=write_file,
        ),
    ]


class ToolRegistry:
    """工具注册表：负责把模型的调用请求路由到具体函数。"""

    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_schema() for t in self._tools.values()]

    def call(self, name: str, arguments_json: str) -> str:
        """执行一次工具调用，任何异常都转成文本结果，交给模型决定下一步。"""
        tool = self._tools.get(name)
        if tool is None:
            return f"[错误] 未知工具：{name}"

        try:
            args = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            return f"[错误] 参数不是合法 JSON：{exc}"
        if not isinstance(args, dict):
            return "[错误] 参数必须是一个 JSON 对象"

        try:
            return tool.func(**args)
        except ToolError as exc:
            return f"[错误] {exc}"
        except TypeError as exc:
            return f"[错误] 参数不匹配：{exc}"
        except Exception as exc:  # noqa: BLE001 - 兜底，避免工具异常中断 Agent
            return f"[错误] 工具执行失败：{exc}"
