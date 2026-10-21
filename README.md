# Milk Dragon · mcp-chat-agent

> 《AI 智能体》课程作业 —— 一个「会说也会做」的中文聊天智能体
> 基于 **MCP（Model Context Protocol）** 构建，用自然语言驱动真实的工具执行

一只憨萌的小恐龙「Milk Dragon」，你跟它聊天，它替你干活：算数学、查天气、画图表、做梗图、记待办、读写文件、帮你决定今天吃什么。

```
你  ❯ 帮我查上海未来三天天气，然后画成柱状图

Milk Dragon ❯ 收到收到，龙龙马上办！用「查天气」来做这一步。
     ▶ 做：get_weather(days=3, unit='c', city='上海')
     ✓ 得：上海 现在 18℃，晴                          ·mcp
     · 想：本龙先去看看～用「生成图表」来做这一步。
     ▶ 做：make_chart(kind='bar', title='上海气温趋势',
                     labels=['10-21','10-22','10-23'], values=[21,21,22])
     ✓ 得：画好了柱状图「上海气温趋势」，共 3 张数据：chart_xxx.png  ·mcp

Milk Dragon ❯ 1. 上海 现在 18℃，晴
        体感 15℃，湿度 64%，风速 18 km/h
        2. 画好了柱状图「上海气温趋势」，共 3 组数据
```

`·mcp` 表示这次工具调用**真的穿过了一个 MCP 协议的 stdio 子进程**，不是本地函数调用。

---

## 目录结构

说明文档在仓库根目录，**所有代码在 `3124004444/` 子目录**。

```
.
├── README.md                 ← 你正在看的这份说明文档
├── docs/
│   ├── blog.md               ← 博客园随笔正文（可直接整篇发布）
│   ├── architecture.md       ← 架构图与流程图（Mermaid）
│   ├── demo_script.md        ← 答辩演示脚本 + 可能被问到的问题
│   ├── demo_transcript.md    ← 8 组演示的真实执行记录（自动生成）
│   ├── 小组分工.md
│   ├── 成员心得.md
│   ├── 学习资源.md
│   └── images/               ← 演示图片
└── 3124004444/               ← 全部可运行代码
    ├── agent/                ← 智能体内核（不依赖 UI、不依赖 mcp）
    ├── mcp_server/           ← 把工具以 MCP 协议暴露出去
    ├── tests/                ← 203 项测试
    ├── scripts/              ← 环境自检、演示脚本
    └── data/                 ← 运行时产物（不入库）
```

---

## 快速开始

```bash
cd 3124004444

# 1. 虚拟环境
python -m venv .venv
.venv\Scripts\activate            # PowerShell / cmd
# source .venv/Scripts/activate   # Git Bash

# 2. 依赖（核心只有 mcp + pillow + matplotlib）
pip install -r requirements.txt

# 3. 环境自检：一次看清 Python / 依赖 / 中文字体 / 网络 / MCP 握手
python scripts/check_env.py
```

自检正常后，直接进入对话：

```bash
python -m agent.ui.cli
```

### 三种运行方式

```bash
python -m agent.ui.cli                     # ① 默认：自动选择工具通道
python -m agent.ui.cli --channel mcp       # ② 强制走 MCP 协议
python -m agent.ui.cli --channel builtin   # ③ 完全不加载 mcp 包

python -m agent.ui.cli --demo              # 一次跑完 8 组演示
python -m agent.ui.cli -c "北京天气怎么样"   # 单条指令
python -m agent.ui.cli --no-network        # 断网模式（天气走示例数据）
```

### 网页端

```bash
pip install -r requirements-web.txt
streamlit run agent/ui/web_app.py
```

### 想接大模型？

默认**不需要任何 API Key** 就能完整运行（内置离线规则式规划器）。想用真大模型：

```bash
cp .env.example .env
# 编辑 .env：AGENT_MODE=llm，填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
```

支持任何 OpenAI 兼容服务（DeepSeek、智谱 GLM、阿里百炼、本地 vLLM），**代码一行都不用改**。
Key 填错或网络不通时会自动退回规则式规划器，不会崩。

---

## 能力一览

| 工具 | 说明 | 依赖 |
|---|---|---|
| `calculate` | 安全计算数学表达式（AST 白名单，禁任意代码执行） | 标准库 |
| `get_weather` | 查天气，三级降级：wttr.in → open-meteo → 离线示例 | 标准库 |
| `make_chart` | 柱状图 / 折线图 / 饼图，中文不乱码 | matplotlib |
| `make_meme` | 梗图 / 便签 / 海报 / 恐龙海报，含原创像素小恐龙 | Pillow |
| `make_ascii_art` | 纯标准库生成 ASCII 艺术字（兜底方案） | 标准库 |
| `todo` | 待办清单增删改查，JSON 持久化 | 标准库 |
| `files` | 在沙箱目录内读写文本文件 | 标准库 |
| `fun_pick` | 掷骰子 / 抛硬币 / 今天吃什么 / 转盘 | 标准库 |

只有两个工具需要第三方库，所以即使装包失败，仍有六个工具可用。

---

## 设计要点

| 方面 | 做法 | 为什么 |
|---|---|---|
| **协议** | 官方 SDK `mcp==2.3.0`，`MCPServer` + `run()`（stdio） | 课程推荐框架；2.x 与 1.x API 不兼容，必须锁版本 |
| **工具定义** | `ToolSpec.input_schema` 唯一真源，MCP 侧只写类型适配的薄壳 | 避免「schema 和实现各写一遍然后不同步」 |
| **一致性保障** | `tests/test_schema_parity.py` 真跑 `tools/list` 逐字段比对 | 用测试钉死，比用反射取巧可靠 |
| **规划** | 规则式（默认）/ LLM function calling（可选），共用同一个 `Planner` 接口 | 无 Key 也能演示；换模型主循环零改动 |
| **多步** | 每次只执行一个动作，基于上一步真实结果再规划 | 「查天气→画图」用的是真数据，不是假动画 |
| **安全** | 计算器 AST 白名单；文件路径白名单沙箱 | 智能体"能动手"就意味着有破坏力 |
| **降级** | 天气三级降级；工具通道 auto→builtin | 演示现场没网没 Key 也不会垮 |
| **生图** | Pillow + matplotlib 本地渲染 | 零成本、不依赖外部图片 API |

> **版权说明**：人格设定参考了常见的憨萌卡通风格，但项目**不包含任何第三方卡通形象的素材**——吉祥物由 `make_meme` 用几何图形逐块绘制，属原创图形。

---

## 测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

```
203 passed, 1 skipped
```

| 测试文件 | 覆盖内容 |
|---|---|
| `test_calc_safety.py` | 18 条恶意表达式必须被拒绝 |
| `test_sandbox.py` | 35 条路径绕过用例（`..`、盘符、UNC、`CON`…） |
| `test_tools_contract.py` | 八个工具的 schema 合法性、空参数不崩 |
| `test_planner_intents.py` | 51 条中文说法，意图 ≥90% / 参数 ≥85% 门槛 |
| `test_schema_parity.py` | MCP `tools/list` 与注册表逐字段一致 |
| `test_mcp_smoke.py` | 真起子进程走 stdio 完成 list/call |
| `test_agent_loop.py` | 多步、错误自纠、步数上限、`$ref` 解析 |
| `test_web_app.py` | 用 Streamlit `AppTest` 无头跑通一次完整对话 |

生成博文素材（执行记录 + 图片）：

```bash
python scripts/demo.py --offline     # 离线跑，结果可复现
python scripts/demo.py               # 联网跑，天气取实时
```

---

## 提交历史

按功能拆成 12 次提交，每次都保证 `pytest` 全绿、代码可运行：

| # | 提交 | 内容 |
|---|---|---|
| 1 | `chore: 初始化仓库结构与依赖清单` | 目录骨架、依赖清单、环境自检脚本 |
| 2 | `feat(core): 工具契约、注册表与路径沙箱` | `ToolSpec`/`ToolResult`、注册表、沙箱 |
| 3 | `feat(tools): 安全计算器与趣味决策` | AST 白名单求值、骰子/午餐 |
| 4 | `feat(tools): 天气查询（三级降级）与待办清单` | wttr.in→open-meteo→示例、JSON 持久化 |
| 5 | `feat(tools): 中文图表、梗图与 ASCII 艺术` | 字体探测链、Pillow 版式、点阵字 |
| 6 | `feat(tools): 沙箱化文件读写` | 白名单路径校验 |
| 7 | `feat(mcp): 以 stdio MCP Server 暴露全部工具` | 薄封装 + 契约测试 + 端到端冒烟 |
| 8 | `feat(planner): 可解释的规则式规划器` | 六段流水线、五路打分、多轮补槽 |
| 9 | `feat(agent): ReAct 主循环、MCP Host 与降级通道，提供 CLI 聊天` | 事件循环、通道降级、记忆 |
| 10 | `feat(ui): Streamlit 网页端与大模型模式零改码切换` | 网页、LLM 规划器、演示脚本 |
| 11 | `docs: 说明文档、博文、分工与成员心得` | 本文档与 `docs/` |
| 12 | `refactor: 智能体更名为 Milk Dragon` | 全局更名，含 MCP 服务端标识与演示素材 |

增量可运行的技巧：`registry.TOOL_MODULES` 是一个随提交增长的列表，未实现的工具不会被 import。

---

## 常见问题

**Q：`ModuleNotFoundError: No module named 'mcp.server.fastmcp'`**
照 1.x 教程写了。现在 `pip install mcp` 装的是 2.x，`FastMCP` 已改名为 `MCPServer` 且旧路径被删除。本项目已锁定 `mcp==2.3.0`。

**Q：中文变成方块**
运行 `python scripts/check_env.py`，看「中文字体」一节的探测结果。我们采用的是「真的渲染一个『测』字看有没有像素」的判定方式，比检查文件存在可靠。

**Q：MCP 连不上 / 一直超时**
自动降级会接管（界面显示 `via=builtin`）。要排查可看 `3124004444/data/state/mcp_server.log`。强制模式 `--channel mcp` 会把原始错误抛出来。

**Q：Streamlit 侧栏工具列表是空的**
说明 Agent 初始化失败，页面上会有 warning。先跑命令行版确认 `check_env.py` 通过。

**Q：任务管理器里有一堆 python.exe**
正常时只有主进程 + 一个 MCP 服务端。如果越积越多，通常是没装 Streamlit 的 `@st.cache_resource` 导致的重复初始化——本项目已经处理。

---

## 详细文档

- 需求 / 业务流 / 实现 / 示例 / 分工 / 心得 / 资源：[`docs/blog.md`](docs/blog.md)
- 架构图与流程图：[`docs/architecture.md`](docs/architecture.md)
- 答辩演示脚本：[`docs/demo_script.md`](docs/demo_script.md)
- 小组成员分工：[`docs/小组分工.md`](docs/小组分工.md)
- 成员心得：[`docs/成员心得.md`](docs/成员心得.md)
- 学习资源推荐：[`docs/学习资源.md`](docs/学习资源.md)
