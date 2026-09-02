"""阶段一完整流程真实测试：接收 -> 归档/去重/决策 -> 模型回复 -> 发送 -> 回读确认。

需要真实 Windows 微信环境、已配置的模型密钥和授权发送的会话，不能作为常规
unittest 运行：python tests/manual_acceptance_full_flow.py [群名或昵称]
"""

import logging
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL  # noqa: E402
from mvp_core.runtime import StageOneRuntime  # noqa: E402
from openai import OpenAI  # noqa: E402
from wxbot import WeChat  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fullflow")

TARGET = sys.argv[1] if len(sys.argv) > 1 else "记录"
MODEL_TIMEOUT = 40
FALLBACK_REPLY = "[wxxixi全流程回复] 收到，这是机器人自动回复测试。"


def _resolve_uname(wx, target):
    for hit in wx._db.search_contact(target):
        if target in (hit.get("nick_name") or "") or target in (hit.get("remark") or ""):
            return hit["username"]
    return None


def _read_rows(wx, uname, needle, attempts=12):
    rows = []
    for attempt in range(attempts):
        try:
            rows = wx._db.get_messages(uname, limit=200)
            break
        except RuntimeError as exc:
            log.warning("回读重试 %d/%d: %s", attempt + 1, attempts, exc)
            time.sleep(5)
    return [row for row in rows if needle in (row.get("content") or "")]


def _generate_reply(text):
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    prompt = (
        f"你正在微信群里做收发流程测试。用户刚发来：{text[:200]}\n"
        "请用一两句话自然回复，不要提到测试本身。"
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=200,
            timeout=MODEL_TIMEOUT,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content:
            return content, "model"
        log.warning("模型返回空内容，使用备用回复")
    except Exception as exc:
        log.warning("模型调用失败，使用备用回复: %s", exc)
    return FALLBACK_REPLY, "fallback"


def main() -> None:
    wx = WeChat()
    log.info("NICK %s", wx.nickname)

    uname = _resolve_uname(wx, TARGET)
    if not uname:
        raise RuntimeError(f"找不到目标会话: {TARGET}")
    log.info("UNAME %s", uname)

    runtime = StageOneRuntime(REPO_ROOT, keywords=("机器人", "西西"))
    send_lock = threading.Lock()

    def receive(message, chat):
        with send_lock:
            result = runtime.handle(message, chat, bot_name=wx.nickname)
            log.info(
                "PIPELINE action=%s accepted=%s duplicate=%s reason=%s type=%s",
                result.action.value,
                result.accepted,
                result.duplicate,
                result.reason,
                getattr(message, "type", "unknown"),
            )
            if not result.accepted or result.action.value != "reply":
                return

            text = getattr(message, "content", "") or ""
            reply, source = _generate_reply(text)
            log.info("REPLY_SOURCE=%s REPLY_TEXT=%r", source, reply[:100])
            started = time.perf_counter()
            resp = wx.SendMsg(reply, who=chat.who)
            elapsed = time.perf_counter() - started
            log.info("SEND resp=%r elapsed=%.2f", resp, elapsed)

            time.sleep(3)
            hits = _read_rows(wx, uname, reply[:40])
            log.info("REPLY_COUNT %d", len(hits))

    registered = wx.AddListenChat(nickname=TARGET, callback=receive)
    if not registered:
        raise RuntimeError(f"无法监听目标会话: {TARGET}")
    log.info("READY target=%s mode=full-flow", TARGET)
    wx.KeepRunning()


if __name__ == "__main__":
    main()
