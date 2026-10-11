# 代码解释 Agent（Code Explain Agent）

**作者**：陆兵尧（2412190705）
**仓库地址**：https://github.com/ReStart66/2412190705-code-explain-agent

一个命令行交互的**代码解释助手**：你把源码文件丢给它，它会先读取真实代码，再解释代码的作用与主要逻辑，并可按需生成注释。项目实现了一个完整的 **Agent 循环（输入 → 推理 → 工具调用 → 输出）**，属于「Homework 1 - Code Agent」中的「代码解释 Agent」方向。

> **零第三方依赖**：只用 Python 标准库实现，不需要 `pip install` 任何包。

---

## 功能

- **解释代码**：说明文件整体作用、执行流程，并指出关键函数/变量与行号。
- **回答问题**：可针对某个函数追问，例如「它是怎么做重试的？」。
- **生成注释**：按需为代码生成中文注释并写回文件（需要你明确要求）。
- **工具调用**：内置 3 个工具，Agent 会自主决定调用哪个：

  | 工具 | 作用 |
  |------|------|
  | `read_file` | 读取源码（带行号，支持分段读取） |
  | `list_files` | 浏览目录，定位文件 |
  | `write_file` | 把结果写回文件 |

- **上下文记忆**：同一会话内保留历史，可以连续追问。
- **错误处理与重试**：网络/超时/限流自动指数退避重试；4xx（如 Key 错误）立即报错；工具异常统一转成文本回传给模型自我修正。

---

## 作业要求对照

| 作业要求 | 实现情况 | 代码位置 |
|---------|---------|---------|
| 基本 Agent 循环（输入 → 推理 → 工具调用 → 输出） | 完整实现，含 ReAct 推理-行动-观察循环 | `core/agent.py` |
| 支持至少一种工具 | 提供 3 种：读文件 / 浏览目录 / 写文件 | `core/tools.py` |
| 可通过命令行交互 | 命令行 REPL，支持 `/help` `/clear` `/exit` | `main.py` |
| LLM 调用 | OpenAI 兼容接口，标准库手写 HTTP 请求 | `core/llm.py` |
| Prompt 设计 | System Prompt 集中管理，约束「先读再解释」 | `core/prompts.py` |
| 支持上下文记忆 | 保留完整对话历史，支持连续追问 | `core/agent.py` |
| 错误处理与重试机制 | LLM 指数退避重试（4xx 不重试）；工具异常转文本回传 | `core/llm.py`、`core/tools.py` |
| 边界情况处理 | 路径越界拦截、文件大小上限、非法参数、未知工具 | `core/tools.py` |
| 技术文档 | 本 README + `Design.md` 设计文档 | `README.md`、`Design.md` |

## 快速开始

### 环境要求

- **Python 3.10+**
- 一个 OpenAI 兼容服务的 **API Key**（OpenAI / DeepSeek / 通义千问 / Moonshot 等均可）

### 1. 配置 API Key

复制 `.env.example` 为 `.env`：

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

编辑 `.env`，填入你的 Key：

```ini
LLM_API_KEY=你的key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

常见服务商配置：

| 服务商 | LLM_BASE_URL | LLM_MODEL |
|--------|--------------|-----------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |

> 注：`api.openai.com` 在国内网络环境下无法直连（连接会被重置）。国内用户建议使用
> DeepSeek / 通义千问 / Moonshot 等服务；`.env.example` 的默认值已设为 DeepSeek。

### 2. 运行

在项目根目录直接运行（此时工作区就是项目根目录）：

```bash
python main.py
```

> **关于 `-w / --workspace`**：它是 Agent 的「代码根目录」，所有文件路径都相对于它。
> 上面的命令里工作区是项目根目录，所以要写 `examples/sample_code.py`。
> 若改用 `python main.py -w ./examples`（权限更收敛），文件路径就要写 `sample_code.py`。
> 用错路径也没关系——Agent 会读取失败后自己用 `list_files` 找到正确位置（见下方「运行效果」）。

### 3. 试一试

```text
你 > 解释 examples/sample_code.py
你 > 这个文件里的 fetch_data 是怎么做重试的？
你 > 帮我给 examples/sample_code.py 加上中文注释
```

---

## 运行效果

Agent 会把工具调用过程打印出来，可以直观看到推理链路：

```text
你 > 解释 examples/sample_code.py
  [第 1 步] 调用工具 read_file({"path": "examples/sample_code.py"})
           ↳ # examples/sample_code.py（共 59 行，显示 1-59 行）

助手 > 这个文件实现了一个带「重试 + 超时」的 HTTP 请求封装……
```

**自我纠错演示**（故意给错路径时，真实运行结果）：

```text
你 > 解释 examples/sample_code.py
  [第 1 步] read_file({"path": "examples/sample_code.py"})  -> [错误] 文件不存在
  [第 2 步] list_files({"path": "."})                      -> 自己发现路径写错了
  [第 3 步] read_file({"path": "sample_code.py"})          -> 成功读取

助手 > 你给的路径是 examples/sample_code.py，但工作区里没有 examples/ 目录，
        文件实际在根目录 sample_code.py。下面按这个文件来解释……
```

---

## 离线自检

不需要 API Key、不需要联网，即可验证 Agent 循环、工具逻辑与 HTTP 层：

```bash
python selftest.py
```

它用「脚本化假 LLM」和「假 transport」跑完整流程，覆盖 18 项检查：

- Agent 循环：推理 → 工具调用 → 再推理 → 输出
- 协议字段：`tool_calls`、`tool_call_id` 是否完整
- 边界情况：路径越界、文件不存在、非法 JSON、未知工具
- HTTP 层：请求构造、响应解析、401 不重试、503 自动重试

预期输出全部 `PASS`。

---

## 项目结构

```
code_explain_agent/
├── main.py              # 命令行入口：参数解析 + REPL 交互
├── selftest.py          # 离线自检（假 LLM + 假 transport）
├── requirements.txt     # 说明：本项目零第三方依赖
├── .env.example         # 配置模板（复制为 .env 使用）
├── core/
│   ├── config.py        # 极简 .env 解析（零依赖）
│   ├── llm.py           # LLM 客户端：urllib 调用 + 重试
│   ├── prompts.py       # Prompt 集中管理
│   ├── tools.py         # 工具定义与注册表
│   └── agent.py         # Agent 核心循环
└── examples/
    └── sample_code.py   # 演示用示例代码
```

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `/help` | 查看帮助 |
| `/clear` | 清空上下文，开始新话题 |
| `/exit` | 退出 |

命令行参数：

| 参数 | 说明 |
|------|------|
| `-w, --workspace` | Agent 可访问的代码目录（默认当前目录） |
| `-q, --quiet` | 不打印工具调用过程 |

---

## 安全说明

- 所有文件操作都被限制在 `--workspace` 指定的目录内，越界路径会被直接拒绝。
- 单个文件读取上限 200KB，超过会提示分段读取，避免超出模型上下文。
- `write_file` 会覆盖原文件，请在使用前确认，或先备份。

---

## 常见问题

**Q：提示「未找到 API Key」？**
确认已把 `.env.example` 复制为 `.env` 并填写 `LLM_API_KEY`，且 `.env` 与 `main.py` 在同一目录。

**Q：提示「接口返回 401」？**
API Key 无效或已过期；若使用第三方服务，请同时确认 `LLM_BASE_URL` 填对了。

**Q：模型不支持工具调用（function calling）？**
请更换支持 function calling 的模型，例如 `gpt-4o-mini`、`deepseek-chat`、`qwen-plus`。可先用 `python main.py -q` 观察是否只调用了工具。

**Q：真的是零依赖吗？**
是。`core/llm.py` 用标准库 `urllib` 直接调用 `POST {base_url}/chat/completions`，`core/config.py` 自己解析 `.env`，全程不依赖任何第三方包。

**Q：想让它解释不在工作区里的文件？**
把 `--workspace` 指到那个文件的上级目录，或在项目目录下启动。
