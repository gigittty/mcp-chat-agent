# 团子龙 · mcp-chat-agent

> 《AI 智能体》课程作业 —— 一个「会说也会做」的聊天智能体
> 基于 **MCP（Model Context Protocol）** 构建，用自然语言驱动真实的工具执行。

一只憨萌的小恐龙「团子龙」，你跟它聊天，它替你干活：算数学、查天气、画图表、做梗图、记待办、读写文件、帮你决定今天吃什么。

```
你  ❯ 帮我查上海未来三天天气，然后画成柱状图

团子龙 ❯ 嗷呜！本龙先去看看上海的天气…
        [调用 get_weather(city=上海, days=3)]  ✓
        再把这几天画成柱状图！
        [调用 make_chart(kind=bar, 数据来自上一步)]  ✓
        画好啦～ 上海未来三天最高温 21/23/19℃，图在下面 ↓
        data/out/chart_20261021_174530.png
```

---

## 目录结构

说明文档在仓库根目录，**所有代码在 `3124004444/` 子目录**。

```
.
├── README.md              ← 你正在看的这份说明文档
├── docs/                  ← 博客园随笔、架构图、演示脚本、分工与心得
└── 3124004444/            ← 全部可运行代码
    ├── agent/             ← 智能体内核（不依赖 UI、不依赖 mcp 包）
    ├── mcp_server/        ← 把工具以 MCP 协议暴露出去
    ├── tests/             ← 单元测试
    └── scripts/           ← 环境自检、演示脚本
```

---

## 快速开始

```bash
# 1. 进入代码目录
cd 3124004444

# 2. 建虚拟环境（Windows）
python -m venv .venv
.venv\Scripts\activate          # PowerShell/cmd
# source .venv/Scripts/activate # Git Bash

# 3. 装依赖
pip install -r requirements.txt

# 4. 环境自检（缺什么一眼看清）
python scripts/check_env.py
```

### 三种运行方式

```bash
# ① 命令行聊天（默认：离线规则模式 + 自动选择工具通道）
python -m agent.ui.cli

# ② 强制走 MCP 协议（答辩时用来证明「真的在用 MCP」）
python -m agent.ui.cli --channel mcp

# ③ 网页界面（需额外装 streamlit）
pip install -r requirements-web.txt
streamlit run agent/ui/web_app.py
```

### 想接大模型？

默认**不需要任何 API Key** 也能完整运行（内置离线规则式规划器）。想用真大模型：

```bash
cp .env.example .env
# 编辑 .env：AGENT_MODE=llm，填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
```

支持任何 OpenAI 兼容服务（DeepSeek、智谱 GLM、阿里百炼等），**代码一行都不用改**。

---

## 能力一览

| 工具 | 说明 |
|---|---|
| `calculate` | 安全计算数学表达式（AST 白名单，禁任意代码执行） |
| `get_weather` | 查天气，三级降级：wttr.in → open-meteo → 离线示例 |
| `make_chart` | 生成柱状图/折线图/饼图（中文字体不乱码） |
| `make_meme` | 生成梗图/海报，含原创像素小恐龙图案 |
| `make_ascii_art` | 纯标准库生成 ASCII 艺术字（兜底方案） |
| `todo` | 待办清单增删改查，JSON 持久化 |
| `files` | 在沙箱目录内读写文本文件 |
| `fun_pick` | 掷骰子 / 抛硬币 / 今天吃什么 / 转盘 |

---

## 技术选型

| 方面 | 选择 | 原因 |
|---|---|---|
| 协议 | MCP 官方 Python SDK `mcp==2.3.0` | 课程推荐框架；工具以标准协议暴露，任何 MCP Host 可复用 |
| 规划 | 规则式（默认）/ LLM function calling（可选） | 无 Key 也能演示；两者共用同一接口，换模型不改内核 |
| 生图 | Pillow + matplotlib 本地渲染 | 零成本、不依赖外部图片 API |
| 界面 | CLI + Streamlit | 命令行好用，网页好截图 |

> **版权说明**：人格设定参考了常见的憨萌卡通风格，但项目**不包含任何第三方卡通形象的素材**——吉祥物由 `make_meme` 用代码逐像素绘制，属原创图形。

---

## 详细文档

- 需求描述、业务流、实现说明、使用示例、小组分工与心得：[`docs/blog.md`](docs/blog.md)
- 架构图与时序图：[`docs/architecture.md`](docs/architecture.md)
- 答辩演示脚本：[`docs/demo_script.md`](docs/demo_script.md)

## 运行测试

```bash
pip install -r requirements-dev.txt
pytest -q
```
