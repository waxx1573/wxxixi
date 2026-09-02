"""阶段一真实微信只读验收监听器：归档消息，不发送回复。

需要真实 Windows 微信环境，不能作为常规 unittest 运行：
python tests/manual_acceptance_listener.py [群名或昵称]
"""

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mvp_core.runtime import StageOneRuntime  # noqa: E402
from wxbot import WeChat  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("acceptance")
TARGET = sys.argv[1] if len(sys.argv) > 1 else "记录"


def main() -> None:
    wx = WeChat()
    runtime = StageOneRuntime(REPO_ROOT, keywords=("机器人", "西西"))

    def receive(message, chat):
        result = runtime.handle(message, chat, bot_name=wx.nickname)
        log.info(
            "REAL_MESSAGE action=%s accepted=%s duplicate=%s reason=%s type=%s",
            result.action.value,
            result.accepted,
            result.duplicate,
            result.reason,
            getattr(message, "type", "unknown"),
        )

    registered = wx.AddListenChat(nickname=TARGET, callback=receive)
    if not registered:
        raise RuntimeError(f"无法监听目标会话: {TARGET}")
    log.info("READY target=%s mode=receive-only", TARGET)
    wx.KeepRunning()


if __name__ == "__main__":
    main()
