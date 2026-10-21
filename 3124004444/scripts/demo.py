"""一键跑完 8 组演示，并生成可直接贴进博文的素材。

    python scripts/demo.py                  # 联网跑（天气取实时数据）
    python scripts/demo.py --offline        # 全程离线（答辩兜底方案）
    python scripts/demo.py --channel mcp    # 强制走 MCP 协议

产出：
- ``docs/images/demo_XX_*.png``  生成的图片（可直接写进博文）
- ``docs/demo_transcript.md``    带每一步「想/做/得」的执行记录
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_ROOT.parent
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from agent.agent import Agent                      # noqa: E402
from agent.config import load_settings, use_settings  # noqa: E402
from agent.contracts import ToolCall               # noqa: E402
from agent.mcp_host.channel import ToolChannel     # noqa: E402
from agent.memory import Memory                    # noqa: E402
from agent.planner.mock_planner import MockPlanner  # noqa: E402

IMAGES_DIR = REPO_ROOT / "docs" / "images"
TRANSCRIPT = REPO_ROOT / "docs" / "demo_transcript.md"

DEMO = [
    ("计算", "算一下 (12+8)*3/5 保留两位小数"),
    ("实时天气", "北京今天天气怎么样"),
    ("多步：查天气再画图", "帮我查上海未来三天天气，然后画成柱状图"),
    ("生成梗图", "画个梗图：周五了，代码还没写完"),
    ("待办：添加", "记一下：周四交作业"),
    ("待办：查看", "列出待办"),
    ("趣味决策", "今天吃什么，帮我选一个"),
    ("ASCII 兜底", "把 HI 转成字符画"),
]


class Recorder:
    """把执行过程收集成 Markdown。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def on_thought(self, text: str, step: int) -> None:
        if text:
            self.lines.append(f"  - 想：{text}")

    def on_action(self, call: ToolCall, step: int) -> None:
        args = ", ".join(f"{key}={value!r}" for key, value in call.arguments.items())
        self.lines.append(f"  - 做：`{call.name}({args})`")

    def on_observation(self, call: ToolCall, result, step: int) -> None:
        mark = "✓" if result.ok else "✗"
        first = (result.text or result.error or "").splitlines()[0]
        self.lines.append(f"  - 得 {mark}：{first}　`via={result.via}`")

    def on_final(self, text: str) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="跑一遍完整演示并生成博文素材")
    parser.add_argument("--channel", choices=["auto", "mcp", "builtin"], default="builtin")
    parser.add_argument("--offline", action="store_true", help="不联网")
    parser.add_argument("--keep-images", action="store_true", help="保留 docs/images 里的旧图")
    args = parser.parse_args()

    settings = load_settings()
    settings.mcp_channel = args.channel
    settings.offline = args.offline or settings.offline
    use_settings(settings)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    if not args.keep_images:
        for stale in IMAGES_DIR.glob("demo_*.png"):
            try:
                stale.unlink()
            except OSError:
                # 文件被占用/无权限时不要中断整场演示，后面覆盖写即可
                print(f"（跳过清理 {stale.name}）")

    channel = ToolChannel(settings.mcp_channel)
    agent = Agent(
        planner=MockPlanner(),
        channel=channel,
        memory=Memory(),
        settings=settings,
        max_steps=settings.max_steps,
    )

    sections: list[str] = [
        "# 团子龙演示记录",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 规划器：{settings.mode_label}",
        f"- 工具通道：{channel.label}",
        f"- 离线模式：{'开' if settings.offline else '关'}",
        f"- 可用工具：{', '.join(spec.name for spec in agent.tools())}",
        "",
    ]

    try:
        for index, (title, text) in enumerate(DEMO, start=1):
            recorder = Recorder()
            agent.observer = recorder
            result = agent.chat(text)

            sections.append(f"## {index}. {title}")
            sections.append("")
            sections.append(f"**用户**：{text}")
            sections.append("")
            sections.append("**执行过程**：")
            sections.extend(recorder.lines or ["  - （无工具调用）"])
            sections.append("")
            sections.append("**回复**：")
            sections.append("")
            sections.append("> " + result.reply.replace("\n", "\n> "))
            sections.append("")

            for artifact_index, artifact in enumerate(result.artifacts, start=1):
                source = Path(artifact.path)
                if artifact.kind != "image" or not source.is_file():
                    continue
                target = IMAGES_DIR / f"demo_{index:02d}_{artifact_index}.png"
                shutil.copy2(source, target)
                sections.append(f"![{title}](images/{target.name})")
                sections.append("")

            print(f"[{index}/{len(DEMO)}] {title} —— {len(result.observations)} 次工具调用")

        TRANSCRIPT.write_text("\n".join(sections), encoding="utf-8")
        print(f"\n执行记录：{TRANSCRIPT}")
        print(f"图片目录：{IMAGES_DIR}")
        return 0
    finally:
        agent.close()
        try:
            from agent.runtime import _default_runtime

            if _default_runtime is not None:
                _default_runtime.shutdown()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
