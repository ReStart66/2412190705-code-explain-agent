"""命令行入口：与「代码解释 Agent」对话。

用法：
    python main.py                        # 允许访问当前目录
    python main.py -w ./examples          # 只允许访问 examples 目录
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.agent import CodeExplainAgent
from core.config import load_dotenv
from core.llm import LLMClient, LLMConfig
from core.tools import ToolRegistry, Workspace, make_tools

# Windows 控制台默认可能是 GBK，这里强制 UTF-8，避免中文输出乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

BANNER = """
============================================================
  代码解释 Agent (Code Explain Agent)
  输入代码文件名，我来解释它的作用和逻辑。
============================================================
  常用操作：
    /help    查看帮助
    /clear   清空对话上下文
    /exit    退出
============================================================
"""

HELP = """使用说明：
1. 直接提问，例如：
     解释 examples/sample_code.py
     这个文件里的 fetch_data 函数是怎么做重试的？
     帮我给 examples/sample_code.py 加上注释
2. Agent 会自动调用 read_file / list_files 等工具读取真实代码，再作答。
3. 写回文件（write_file）需要你在提问里明确要求。
4. 命令：
     /help    显示本帮助
     /clear   清空上下文（开始新话题）
     /exit    退出程序
"""

EXIT_WORDS = {"/exit", "/quit", ":q", "exit", "quit"}


def build_agent(workspace_root: Path, verbose: bool = True) -> CodeExplainAgent:
    """组装 Agent：配置 → LLM 客户端 → 工作区 → 工具 → Agent。"""
    config = LLMConfig.from_env()
    client = LLMClient(config)
    workspace = Workspace(workspace_root)
    tools = ToolRegistry(make_tools(workspace))
    return CodeExplainAgent(client=client, tools=tools, verbose=verbose)


def repl(agent: CodeExplainAgent, workspace_root: Path) -> None:
    print(BANNER)
    print(f"工作区：{workspace_root}")

    while True:
        try:
            text = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            return

        if not text:
            continue
        if text in EXIT_WORDS:
            print("再见！")
            return
        if text == "/help":
            print(HELP)
            continue
        if text == "/clear":
            agent.clear()
            print("已清空对话上下文。")
            continue

        try:
            answer = agent.run(text)
        except Exception as exc:  # noqa: BLE001 - CLI 层兜底，避免一次报错就退出
            print(f"\n[错误] {exc}")
            continue

        print(f"\n助手 > {answer}")


def main() -> int:
    parser = argparse.ArgumentParser(description="代码解释 Agent（Code Explain Agent）")
    parser.add_argument(
        "-w",
        "--workspace",
        default=".",
        help="允许 Agent 访问的代码目录（默认：当前目录）",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="不打印工具调用过程")
    args = parser.parse_args()

    # 读取同目录下的 .env（本项目零第三方依赖，自己解析）
    load_dotenv()

    workspace_root = Path(args.workspace).expanduser().resolve()
    if not workspace_root.exists():
        print(f"[错误] 工作区不存在：{workspace_root}")
        return 1

    try:
        agent = build_agent(workspace_root, verbose=not args.quiet)
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 初始化失败：{exc}")
        return 1

    repl(agent, workspace_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
