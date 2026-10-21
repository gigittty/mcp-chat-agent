# 架构与时序

> 本文用 Mermaid 描述项目结构。GitHub、VS Code、Typora、博客园（需插件）都能直接渲染。

## 1. 分层架构

```mermaid
flowchart TD
    CLI["CLI 终端<br/>agent/ui/cli.py"]
    WEB["Streamlit 网页<br/>agent/ui/web_app.py"]

    AGENT["Agent 主循环<br/>Thought → Action → Observation → Final"]
    PLANNER["规划器 Planner"]
    MOCK["MockPlanner<br/>规则式，无需 Key"]
    LLM["LLMPlanner<br/>OpenAI 兼容 function calling"]

    CHANNEL["工具通道 ToolChannel<br/>auto / mcp / builtin"]
    HOST["MCP Client<br/>stdio 子进程"]
    REG["工具注册表 registry<br/>SPEC 唯一真源"]

    SERVER["MCP Server<br/>mcp_server/"]
    TOOLS["8 个工具"]
    DATA[("data/<br/>out · files · state")]

    CLI --> AGENT
    WEB --> AGENT
    AGENT --> PLANNER
    PLANNER --> MOCK
    PLANNER --> LLM
    AGENT --> CHANNEL
    CHANNEL -->|mcp| HOST
    CHANNEL -->|builtin| REG
    HOST -->|JSON-RPC over stdio| SERVER
    SERVER --> REG
    REG --> TOOLS
    TOOLS --> DATA
```

设计约束：`agent/` 不 import `mcp`、不 import `streamlit`。因此内核可以被 CLI、网页和测试三方复用。

## 2. 一次多步请求的时序

```mermaid
sequenceDiagram
    participant U as 用户
    participant A as Agent
    participant P as MockPlanner
    participant C as ToolChannel
    participant S as MCP Server

    U->>A: 帮我查上海未来三天天气，然后画成柱状图
    A->>P: plan(step=0, observations=[])
    P->>P: 归一化 → 切分 → 打分
    P-->>A: ToolCall(get_weather, city=上海, days=3)
    A->>C: invoke(get_weather)
    C->>S: call_tool
    S-->>C: {ok, data:{series:{date:[...], temp_max:[...]}}}
    C-->>A: ToolResult(via=mcp)
    A->>P: plan(step=1, observations=[天气结果])
    P->>P: 上下文加分 +2.0 → make_chart<br/>槽位直接从 observations 取
    P-->>A: ToolCall(make_chart, labels=[...], values=[...])
    A->>C: invoke(make_chart)
    C->>S: call_tool
    S-->>C: {ok, artifacts:[{path: chart.png}]}
    C-->>A: ToolResult(via=mcp)
    A->>P: plan(step=2, observations=[天气, 图表])
    P-->>A: final_text（总结两步结果）
    A-->>U: 天气 + 图片路径
```

关键点：**第二步的参数来自第一步的真实返回值**，不是一次性生成两个调用。

## 3. 两条降级链

```mermaid
flowchart LR
    subgraph 天气
        W1[wttr.in] -->|超时/失败| W2[open-meteo]
        W2 -->|失败| W3[本地示例数据<br/>明确标注"离线示例"]
    end
```

```mermaid
flowchart LR
    subgraph 工具通道
        T1[auto] -->|握手成功| T2[MCP 子进程]
        T1 -->|握手失败/超时| T3[进程内直调<br/>via=builtin]
        T2 -->|传输层故障| T3
    end
```

## 4. 规划器流水线

```mermaid
flowchart TD
    IN["用户输入"] --> N["normalize<br/>全角→半角 · 中文数字→阿拉伯<br/>去句首礼貌语 · 英文小写"]
    N --> S["segment<br/>按连词切分，括号内不切"]
    S --> SC["score<br/>五路加权打分"]
    SC --> D1{"TOP ≥ 2.6?"}
    D1 -->|否| D2{"TOP ≥ 1.2?"}
    D2 -->|是| CL["clarify<br/>列出候选让用户选"]
    D2 -->|否| CH["闲聊兜底"]
    D1 -->|是| SL["slots<br/>槽位抽取 + 跨步引用"]
    SL --> V{"schema 校验通过?"}
    V -->|缺必填| ASK["ask_user<br/>存 pending 等下一轮补"]
    V -->|通过| CALL["产出 ToolCall"]
```

## 5. 记忆分层

```mermaid
flowchart LR
    SHORT["短期<br/>deque 最近 12 条"]
    PEND["待补槽位<br/>pending"]
    LONG["长期偏好<br/>data/state/memory.json"]
    SHORT --> CTX["PlanContext"]
    PEND --> CTX
    LONG --> CTX
```
