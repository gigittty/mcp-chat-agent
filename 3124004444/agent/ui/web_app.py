"""Streamlit 网页界面。

    streamlit run agent/ui/web_app.py

和 CLI 共用完全相同的 Agent 内核，只是换了个壳：
- 左侧栏展示**动态发现**出来的工具清单（走 MCP 时是真从服务端拉的）
- 每次工具调用用可折叠的状态块展示「想 → 做 → 得」
- 生成的图片直接显示在对话里

一个关键实现点：Agent 与事件循环放在 ``@st.cache_resource`` 里。
Streamlit 每次交互都会重跑整个脚本，如果每次重建 Agent，
就会不停地新起 MCP 子进程，任务管理器里会看到一堆 python.exe。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

# 让 streamlit 直接跑单文件时也能 import 到包
CODE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from agent.agent import Agent                      # noqa: E402
from agent.config import load_settings, use_settings  # noqa: E402
from agent.contracts import ToolCall               # noqa: E402
from agent.mcp_host.channel import ToolChannel     # noqa: E402
from agent.memory import Memory                    # noqa: E402

st.set_page_config(page_title="Milk Dragon · 聊天智能体", page_icon="🐉", layout="wide")

CSS = """
<style>
    .block-container { padding-top: 1.4rem; }
    .tool-card { border-left: 3px solid #7c6cf0; padding: .35rem .7rem;
                 margin: .3rem 0; background: rgba(124,108,240,.06); border-radius: 4px;
                 font-size: .88rem; }
    .via-mcp { color: #16a34a; font-weight: 600; }
    .via-builtin { color: #6b7280; }
    .persona { font-size: .85rem; color: #8b8b8b; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


class StreamlitObserver:
    """把执行过程写进 status 容器。"""

    def __init__(self, container) -> None:
        self.container = container

    def on_thought(self, text: str, step: int) -> None:
        if text:
            self.container.markdown(f"<div class='persona'>💭 {text}</div>", unsafe_allow_html=True)

    def on_action(self, call: ToolCall, step: int) -> None:
        args = json.dumps(call.arguments, ensure_ascii=False)
        self.container.markdown(
            f"<div class='tool-card'>▶ <b>{call.name}</b>({args})</div>", unsafe_allow_html=True
        )

    def on_observation(self, call: ToolCall, result, step: int) -> None:
        css = "via-mcp" if result.via == "mcp" else "via-builtin"
        if result.ok:
            text = (result.text or "").replace("\n", " ")[:220]
            self.container.markdown(
                f"<div class='tool-card'>✓ {text} "
                f"<span class='{css}'>·{result.via}</span></div>",
                unsafe_allow_html=True,
            )
        else:
            self.container.markdown(
                f"<div class='tool-card'>✗ {result.error}</div>", unsafe_allow_html=True
            )

    def on_final(self, text: str) -> None:
        return


@st.cache_resource(show_spinner=False)
def get_agent(mode: str, channel: str, offline: bool, max_steps: int):
    """跨 rerun 复用同一个 Agent（含 MCP 子进程）。"""
    settings = load_settings()
    settings.agent_mode = mode
    settings.mcp_channel = channel
    settings.offline = offline
    settings.max_steps = max_steps
    use_settings(settings)

    if mode == "llm":
        try:
            from agent.planner.llm_planner import LLMPlanner

            planner = LLMPlanner(settings)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"大模型模式初始化失败：{exc}，已退回离线规则模式")
            planner = None
    else:
        planner = None
    if planner is None:
        from agent.planner.mock_planner import MockPlanner

        planner = MockPlanner()

    tool_channel = ToolChannel(settings.mcp_channel)
    agent = Agent(
        planner=planner,
        channel=tool_channel,
        memory=Memory(),
        settings=settings,
        max_steps=max_steps,
    )
    return agent


def render_artifacts(artifacts) -> None:
    for artifact in artifacts:
        path = Path(artifact.path)
        if not path.is_file():
            continue
        if artifact.kind == "image":
            st.image(str(path), caption=artifact.caption or path.name, width=520)
        else:
            st.caption(f"📎 {path.name}")


def main() -> None:
    st.title("🐉 Milk Dragon · 会说也会做的聊天智能体")
    st.caption("自然语言对话 → 意图识别 → 真实工具调用 → 结果回填。试试「帮我查上海未来三天天气，然后画成柱状图」")

    with st.sidebar:
        st.subheader("运行设置")
        mode = st.radio(
            "规划器",
            ["mock", "llm"],
            index=0,
            format_func=lambda value: "离线规则（无需 Key）" if value == "mock" else "大模型（需 API Key）",
        )
        channel = st.radio(
            "工具通道",
            ["auto", "mcp", "builtin"],
            index=0,
            format_func=lambda value: {
                "auto": "自动（优先 MCP）",
                "mcp": "强制 MCP 协议",
                "builtin": "进程内直调",
            }[value],
        )
        offline = st.toggle("离线模式（不联网）", value=False)
        max_steps = st.slider("单轮最大步数", 1, 10, 6)

        if st.button("清空对话", use_container_width=True):
            st.session_state.messages = []
            agent = get_agent(mode, channel, offline, max_steps)
            agent.memory.reset()
            st.rerun()

        st.divider()
        st.subheader("工具清单")
        agent = get_agent(mode, channel, offline, max_steps)
        specs = agent.tools()
        st.caption(f"通道：{agent.channel.label}　共 {len(specs)} 个")
        if agent.channel.fallback_reason:
            st.warning(f"已降级：{agent.channel.fallback_reason}")
        for spec in specs:
            st.markdown(f"- **{spec.title}**　`{spec.name}`")

        st.divider()
        trace_path = CODE_ROOT / "data" / "state" / "last_trace.json"
        if trace_path.is_file():
            st.download_button(
                "下载上一轮执行轨迹",
                data=trace_path.read_text(encoding="utf-8"),
                file_name="last_trace.json",
                mime="application/json",
                use_container_width=True,
            )

    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "嗷呜～本龙在呢！算数、查天气、画图、记待办都能干，说吧说吧。"}
        ]

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("artifacts"):
                render_artifacts(message["artifacts"])

    prompt = st.chat_input("跟 Milk Dragon 说点什么…")
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    agent = get_agent(mode, channel, offline, max_steps)
    with st.chat_message("assistant"):
        with st.status("Milk Dragon 正在处理…", expanded=True) as status:
            agent.observer = StreamlitObserver(status)
            try:
                result = agent.chat(prompt)
            except Exception as exc:  # noqa: BLE001
                status.update(label="出错了", state="error")
                st.error(f"{type(exc).__name__}: {exc}")
                return
            status.update(
                label=f"完成（{len(result.observations)} 次工具调用，用时 {result.trace.get('elapsed_ms', 0)} ms）",
                state="complete",
                expanded=False,
            )

        st.markdown(result.reply)
        render_artifacts(result.artifacts)

        with st.expander("执行轨迹"):
            st.json(result.trace)

    st.session_state.messages.append(
        {"role": "assistant", "content": result.reply, "artifacts": result.artifacts}
    )


if __name__ == "__main__":
    main()
