"""命令行聊天界面。

    python -m agent.ui.cli                      # 交互模式
    python -m agent.ui.cli -c "北京天气怎么样"    # 单条指令
    python -m agent.ui.cli --demo               # 跑一遍固定演示
    python -m agent.ui.cli --channel mcp        # 强制走 MCP 协议
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from agent import persona
from agent.config import get_settings, load_settings
from agent.contracts import ToolCall
from agent.mcp_host.channel import ToolChannel
from agent.memory import Memory
from agent.planner.mock_planner import MockPlanner
from agent.ui import render

DEMO_LINES = [
    "算一下 (12+8)*3/5 保留两位小数",
    "北京今天天气怎么样",
    "帮我查上海未来三天天气，然后画成柱状图",
    "画个梗图：周五了，代码还没写完",
    "记一下：周四交作业",
    "列出待办",
    "今天吃什么，帮我选一个",
    "把 HI 转成字符画",
]


class CliObserver:
    """把 Agent 的执行过程打印到终端。"""

    def __init__(self, debug: bool = False, show_preview: bool = True) -> None:
        self.debug = debug
        self.show_preview = show_preview

    def on_thought(self, text: str, step: int) -> None:
        render.thought(text, step)

    def on_action(self, call: ToolCall, step: int) -> None:
        render.action(call.name, call.arguments)

    def on_observation(self, call: ToolCall, result, step: int) -> None:
        render.observation(result, result.via)
        if self.show_preview and result.artifacts:
            render.artifacts(result)

    def on_final(self, text: str) -> None:
        # 回复统一在 chat() 返回后打印，这里不重复输出
        return


def build_agent(args: argparse.Namespace):
    from agent.agent import Agent

    settings = load_settings()
    if args.channel:
        settings.mcp_channel = args.channel
    if args.mode:
        settings.agent_mode = args.mode
    if args.no_network:
        settings.offline = True
    if args.max_steps:
        settings.max_steps = args.max_steps

    planner = None
    if settings.agent_mode == "llm":
        try:
            from agent.planner.llm_planner import LLMPlanner

            planner = LLMPlanner(settings)
        except Exception as exc:  # noqa: BLE001
            render.write(render.paint(f"  大模型模式初始化失败（{exc}），先退回离线规则模式", render.Style.YELLOW))
            settings.agent_mode = "mock"
    if planner is None:
        planner = MockPlanner()

    channel = ToolChannel(settings.mcp_channel)
    memory = Memory()
    agent = Agent(
        planner=planner,
        channel=channel,
        memory=memory,
        settings=settings,
        observer=CliObserver(debug=args.debug),
        max_steps=settings.max_steps,
    )
    return agent, settings


def run_once(agent, text: str) -> None:
    render.user_echo(text)
    result = agent.chat(text)
    render.reply(result.reply)
    if result.kind == "max_steps":
        render.write(render.paint("  （达到最大步数，先停下来了）", render.Style.DIM))


def repl(agent, settings) -> int:
    while True:
        try:
            text = input(render.paint("你  ❯ ", render.Style.BOLD)).strip()
        except (EOFError, KeyboardInterrupt):
            render.write("\n" + render.paint("  拜拜～本龙去睡觉了。", render.Style.DIM))
            return 0

        if not text:
            continue
        if text in ("/exit", "/quit", "exit", "quit"):
            render.write(render.paint("  拜拜～本龙去睡觉了。", render.Style.DIM))
            return 0
        if text in ("/help", "/h", "?"):
            render.write(render.help_text())
            continue
        if text == "/tools":
            render.write(render.tools_table(agent.tools(), agent.channel.label))
            continue
        if text == "/clear":
            agent.memory.reset()
            render.write(render.paint("  短期记忆已清空（长期偏好保留）", render.Style.DIM))
            continue
        if text == "/trace":
            trace_file = Path(agent.memory._file()).parent / "last_trace.json"
            if trace_file.is_file():
                render.write(trace_file.read_text(encoding="utf-8"))
            else:
                render.write(render.paint("  还没有执行记录", render.Style.DIM))
            continue

        try:
            run_once(agent, text)
        except KeyboardInterrupt:
            render.write(render.paint("\n  已中断这一轮", render.Style.DIM))
        except Exception:  # noqa: BLE001
            render.write(render.paint("  出错了：", render.Style.RED))
            traceback.print_exc()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent.ui.cli",
        description="团子龙 —— 会说也会做的聊天智能体",
    )
    parser.add_argument("-c", "--command", help="只执行一条指令后退出")
    parser.add_argument("--demo", action="store_true", help="依次执行内置演示指令")
    parser.add_argument("--channel", choices=["auto", "mcp", "builtin"], help="工具通道")
    parser.add_argument("--mode", choices=["mock", "llm"], help="规划器模式")
    parser.add_argument("--no-network", action="store_true", help="强制离线（天气走示例数据）")
    parser.add_argument("--max-steps", type=int, default=0, help="单轮最大工具调用步数")
    parser.add_argument("--debug", action="store_true", help="显示规划器打分明细")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # -c 模式的输出常被管道/日志捕获，关掉颜色更干净
    render.init(enabled=not args.command)

    agent, settings = build_agent(args)

    render.banner(settings.mode_label, agent.channel.label, len(agent.tools()))
    if settings.offline:
        render.write(render.paint("  离线模式已开启：天气等联网工具会使用示例数据", render.Style.YELLOW))

    try:
        if args.command:
            run_once(agent, args.command)
            return 0
        if args.demo:
            for line in DEMO_LINES:
                run_once(agent, line)
                render.write(render.paint("─" * 52, render.Style.DIM))
            return 0
        return repl(agent, settings)
    finally:
        agent.close()
        try:
            from agent.runtime import _default_runtime

            if _default_runtime is not None:
                _default_runtime.shutdown()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
