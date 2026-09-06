import asyncio
import json
import urllib.parse
import urllib.request

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter


class Main(star.Star):
    def __init__(self, context: star.Context, config=None):
        super().__init__(context, config or {})
        self.config = config or {}

    @property
    def base_url(self):
        return str(self.config.get("bridge_url", "http://127.0.0.1:8766")).rstrip("/")

    async def _request(self, path, method="GET", payload=None):
        timeout = int(self.config.get("request_timeout", 8))

        def call():
            body = None
            headers = {}
            if payload is not None:
                body = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(self.base_url + path, data=body,
                                         headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return await asyncio.to_thread(call)

    @filter.command("微信桥接", alias={"微信状态"})
    async def bridge_status(self, event: AstrMessageEvent):
        try:
            status = await self._request("/status")
            yield event.plain_result(
                "微信桥接\n"
                f"运行: {status.get('running')}\n"
                f"暂停: {status.get('paused')}\n"
                f"WeFlow: {status.get('weflow_connected')}\n"
                f"AstrBot: {status.get('ob_connected')}\n"
                f"UIA: {status.get('uia_ready', '见桥接日志')}"
            )
        except Exception as exc:
            logger.warning("wechat bridge status failed: %s", type(exc).__name__)
            yield event.plain_result("微信桥接不可达，请检查 Windows 桥接服务")

    @filter.command("微信暂停")
    async def bridge_pause(self, event: AstrMessageEvent):
        yield event.plain_result(await self._control(event, "/pause", "已暂停微信自动回复"))

    @filter.command("微信恢复")
    async def bridge_resume(self, event: AstrMessageEvent):
        yield event.plain_result(await self._control(event, "/resume", "已恢复微信自动回复"))

    @filter.command("微信重连")
    async def bridge_reconnect(self, event: AstrMessageEvent):
        result = await self._control(event, "/start", "已请求桥接重连")
        yield event.plain_result(result)

    @filter.command("微信测试")
    async def bridge_test(self, event: AstrMessageEvent, text: str = ""):
        message = text.strip() or "桥接测试成功"
        contact = str(self.config.get("default_contact", "记录"))
        try:
            await self._request("/api/test-send", "POST", {"contact": contact, "text": message})
            yield event.plain_result(f"已请求向 {contact} 发送测试消息")
        except Exception as exc:
            logger.warning("wechat bridge test failed: %s", type(exc).__name__)
            yield event.plain_result("测试消息发送失败，请检查桥接服务")

    async def _control(self, event, path, success):
        try:
            await self._request(path, "POST")
            return success
        except Exception as exc:
            logger.warning("wechat bridge control failed: %s", type(exc).__name__)
            return "微信桥接不可达，请检查 Windows 桥接服务"
