"""
桥接核心模块：WeFlowBridge 类。

职责：
1. 连接 WeFlow SSE 推送，接收微信消息
2. 消息缓冲合并（BUFFER_SECONDS）
3. 构造 OneBot 事件，推送给 AstrBot
4. 多层消息去重（rawid、内容、自回复）
"""

import base64
import io
import wave
from urllib.parse import urljoin, urlsplit
import json
import logging
import os
import queue
import re
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime

import requests

import state
import config
from ob_protocol import push_event, make_message_event

log = logging.getLogger("ob11-bridge")

_MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_INLINE_AUDIO_BYTES = 8 * 1024 * 1024


def _image_extension(payload: bytes) -> str | None:
    """Identify supported image bytes without trusting URL or Content-Type."""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return ".webp"
    return None


def _image_segment_from_path(image_path: str | None) -> dict | None:
    """Build a portable OneBot image segment for the remote AstrBot host."""
    if not image_path:
        return None
    try:
        size = os.path.getsize(image_path)
        if size <= 0 or size > _MAX_INLINE_IMAGE_BYTES:
            log.warning("微信图片无法内联: size=%s path=%s", size, image_path)
            return None
        with open(image_path, "rb") as image_file:
            payload = image_file.read(_MAX_INLINE_IMAGE_BYTES + 1)
        if not payload or len(payload) > _MAX_INLINE_IMAGE_BYTES:
            return None
    except OSError as exc:
        log.warning("读取微信图片失败: %s (%s)", image_path, type(exc).__name__)
        return None
    encoded = base64.b64encode(payload).decode("ascii")
    return {"type": "image", "data": {"file": f"base64://{encoded}"}}


def _is_voice_message(data: dict) -> bool:
    """WeFlow SSE supplies an exact placeholder; REST also supplies typed fields."""
    kind = data.get("type") or data.get("msgType") or data.get("localType")
    media = str(data.get("mediaType") or "").lower()
    return str(kind) == "34" or media in {"voice", "audio", "record", "sound"} or any(
        str(data.get(key) or "").strip() in {"[语音]", "[语音消息]"}
        for key in ("content", "parsedContent")
    )


def _audio_segment_from_path(audio_path: str | None) -> dict | None:
    """Build a portable OneBot record segment for the remote AstrBot host."""
    if not audio_path:
        return None
    try:
        size = os.path.getsize(audio_path)
        if size <= 0 or size > _MAX_INLINE_AUDIO_BYTES:
            log.warning("微信语音无法内联: size=%s path=%s", size, audio_path)
            return None
        with open(audio_path, "rb") as audio_file:
            payload = audio_file.read(_MAX_INLINE_AUDIO_BYTES + 1)
        if not payload or len(payload) > _MAX_INLINE_AUDIO_BYTES:
            return None
    except OSError as exc:
        log.warning("读取微信语音失败: %s (%s)", audio_path, type(exc).__name__)
        return None
    encoded = base64.b64encode(payload).decode("ascii")
    return {"type": "record", "data": {"file": f"base64://{encoded}"}}


# ============ 桥接核心 ============


class WeFlowBridge:
    """WeFlow ↔ AstrBot 桥接器（OneBot v11 版）。"""

    def __init__(self, sender):
        self.sender = sender
        self.processed_ids = set()
        self.start_timestamp = int(time.time())
        self.pending_buffers = {}
        self.buffer_lock = threading.Lock()
        self.chat_histories = defaultdict(list)
        self.contact_map = {}
        self._sse_session = None
        self._recent_seen = {}
        self._sent_recently = {}
        self._sse_event_keys = {}
        self._pending_image = {}  # talkerId → {"caption": None|str, "event": threading.Event()}
        self._pending_mention_images = {}  # session_id → {"data": data, "time": timestamp} 先图后文暂存

    def resolve_group_contact(self, session_id, fallback=""):
        """Resolve UI target by stable WeFlow identity, never by a member name."""
        if not session_id or "@chatroom" not in session_id:
            return fallback
        now = time.monotonic()
        cached = self.contact_map.get(session_id)
        if cached and now - cached[1] < 300:
            return cached[0]
        try:
            response = requests.get(
                f"{config.WE_FLOW_BASE_URL}/api/v1/contacts",
                params={"access_token": config.ACCESS_TOKEN}, timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data", payload) if isinstance(payload, dict) else payload
            if isinstance(rows, dict):
                rows = rows.get("contacts", rows.get("items", []))
            matches = set()
            for item in rows if isinstance(rows, list) else []:
                if not isinstance(item, dict):
                    continue
                wxid = str(item.get("username") or item.get("wxid") or item.get("id") or item.get("userName") or "").strip()
                if wxid != session_id:
                    continue
                name = str(item.get("remark") or item.get("nickname") or item.get("displayName") or item.get("name") or "").strip()
                if name and "@chatroom" not in name:
                    matches.add(name)
            if len(matches) == 1:
                name = matches.pop()
                self.contact_map[session_id] = (name, now)
                return name
        except Exception as exc:
            log.warning("群名查询失败: %s", type(exc).__name__)
        # A genuine groupName from SSE is usable; a raw ID must fail safely in UIA.
        return fallback or session_id

    def should_ignore(self, data):
        content = data.get("content", "")
        msg_type = data.get("type", 0) or data.get("msgType", 0)
        if data.get("sourceName", "") in config.BOT_NICKNAMES:
            return True
        if config.BOT_WXID and data.get("talkerId", "") == config.BOT_WXID:
            return True
        if _is_voice_message(data):
            return False
        if not content or content.strip() == "":
            return True
        return False

    def _is_mentioned(self, data):
        """检测群消息是否 @ 了机器人。

        WeFlow SSE 推送不含 @ 结构字段，只能从 content 文本检测。
        """
        content = data.get("content", "")
        if not content:
            return False

        for nick in config.BOT_NICKNAMES:
            # 标准 @昵称（ASCII @）
            if f"@{nick}" in content:
                return True
            # 全角 @昵称（部分微信版本可能用全角符号）
            if f"＠{nick}" in content:
                return True

        # 日志：content 以 @ 开头但未匹配到任何昵称（便于排查）
        if (content.startswith("@") or content.startswith("＠")) and len(content) > 1:
            log.debug(f"⚠️ content 以@开头但未匹配昵称: content={content[:40]!r} nicknames={config.BOT_NICKNAMES}")

        return False

    def add_to_buffer(self, data):
        """将消息加入缓冲区，等待合并后统一推送给 AstrBot。"""
        content = data.get("content", "")
        source_name = data.get("sourceName", "") or data.get("talkerName", "") or "未知"

        # 先判断群聊/私聊（图片/表情分支也需要用到）
        session_id_data = data.get("sessionId", "") or source_name
        group_name_raw = data.get("groupName", "")
        is_group = (data.get("sessionType", "") == "group") or bool(group_name_raw) or "@chatroom" in session_id_data

        if is_group and config.ALLOWED_GROUPS:
            group_name = re.sub(r'\s*\(\d+\)\s*$', '', group_name_raw or source_name).strip()
            if group_name not in config.ALLOWED_GROUPS and session_id_data not in config.ALLOWED_GROUPS:
                return

        if _is_voice_message(data):
            if is_group and state.group_reply_mode == "mention" and not self._is_mentioned(data):
                return
            threading.Thread(target=self.process_voice_message, args=(data,), daemon=True).start()
            return

        if content == "[图片]":
            # 图片消息（mention 模式下需 @ 才处理）
            if is_group and state.group_reply_mode == "mention" and not self._is_mentioned(data):
                # 先图后文：暂存图片，等后续同人发 @ 文字时合并
                self._pending_mention_images[session_id_data] = {"data": data, "time": time.time()}
                log.info(f"📸 暂存图片，等待关联 @ 文字 (session={session_id_data})")
                return
            threading.Thread(target=self.process_image_message,
                           args=(data,), daemon=True).start()
            return

        if content in ("[动画表情]", "[表情]"):
            # 表情包消息（mention 模式下需 @ 才处理）
            if is_group and state.group_reply_mode == "mention" and not self._is_mentioned(data):
                # 先图后文：暂存表情，等后续同人发 @ 文字时合并
                self._pending_mention_images[session_id_data] = {"data": data, "time": time.time()}
                log.info(f"😀 暂存表情，等待关联 @ 文字 (session={session_id_data})")
                return
            threading.Thread(target=self.process_emoji_message,
                           args=(data,), daemon=True).start()
            return

        now = time.time()
        if content and content in self._sent_recently and now - self._sent_recently[content] < 120:
            log.info(f"⏭️ 自回复去重跳过: {content[:30]}")
            return

        sender_in_group = data.get("senderName", "") or data.get("sender", "") or data.get("sourceName", "")

        if is_group:
            if state.group_reply_mode == "mention" and not self._is_mentioned(data) and not any(k.casefold() in content.casefold() for k in (config.BOT_NICKNAMES + ["皮皮", "pipi"])):
                log.debug(f"⏭️ mention 模式跳过（未检测到 @）: data keys={list(data.keys())} nickname={config.BOT_NICKNAMES} content={content[:40]}")
                return
            group_raw = group_name_raw or session_id_data
            base_name = re.sub(r'\s*\(\d+\)\s*$', '', group_raw).strip()
            contact = base_name
        else:
            contact = source_name

        if is_group and state.group_reply_mode == "batch":
            buffer_key = f"__batch__{base_name}"
        elif is_group and sender_in_group:
            buffer_key = f"{session_id_data}_{sender_in_group}"
        else:
            buffer_key = session_id_data

        with self.buffer_lock:
            if buffer_key not in self.pending_buffers:
                self.pending_buffers[buffer_key] = {
                    "messages": [],
                    "timer": None,
                    "timer_version": 0,
                    "processing": False,
                    "contact": contact,
                    "is_group": is_group,
                    "source_name": source_name,
                    "group_name": base_name if is_group else "",
                    "sender_in_group": sender_in_group if is_group else "",
                    "session_id_data": session_id_data,
                }
            entry = self.pending_buffers[buffer_key]
            if is_group and state.group_reply_mode == "batch" and sender_in_group:
                entry["messages"].append(f'成员"{sender_in_group}"在群"{base_name}"中对你说：{content}')
            else:
                entry["messages"].append(content)

            if not entry["processing"]:
                if entry["timer"]:
                    entry["timer"].cancel()
                entry["timer_version"] += 1
                version = entry["timer_version"]

                # 检查是否有暂存的图片（先图后文场景）
                has_pending_image = False
                if is_group and state.group_reply_mode == "mention":
                    cached = self._pending_mention_images.pop(session_id_data, None)
                    if cached and time.time() - cached["time"] < 15:
                        has_pending_image = True
                        log.info(f"📸 检测到关联图片，延长缓冲等待描述")
                        # 异步下载并描述图片，完成后注入 buffer
                        threading.Thread(
                            target=self._inject_cached_image,
                            args=(cached["data"].get("sessionId", session_id_data),
                                  cached["data"], buffer_key, version),
                            daemon=True,
                        ).start()

                buffer_delay = 15 if has_pending_image else config.BUFFER_SECONDS
                log.info(f"📩 收到来自 {contact} 的消息，等待 {buffer_delay}s 后统一推送")
                timer = threading.Timer(buffer_delay, lambda v=version, sid=buffer_key: self.process_sender(sid, v))
                timer.daemon = True
                timer.start()
                entry["timer"] = timer

    def process_sender(self, sender_id, version=None):
        """缓冲到期：通过 OneBot 事件推送给 AstrBot。"""
        with self.buffer_lock:
            if sender_id not in self.pending_buffers:
                return
            entry = self.pending_buffers[sender_id]
            if version is not None and entry.get("timer_version", 0) != version:
                return
            if not entry["messages"] and not entry.get("segments"):
                return
            msgs = entry["messages"].copy()
            media_segments = list(entry.get("segments", []))
            entry["messages"] = []
            entry["segments"] = []
            entry["processing"] = True
            if entry["timer"]:
                entry["timer"].cancel()
                entry["timer"] = None

        contact = entry.get("contact", sender_id)
        is_group = entry.get("is_group", False)
        if is_group:
            session_id = entry.get("session_id_data", "")
            group_name = entry.get("group_name", "")
            # Missing SSE names can be the sender nickname or the raw chatroom ID.
            fallback = group_name if group_name and "@chatroom" not in group_name else session_id
            contact = self.resolve_group_contact(session_id, fallback)
            entry["contact"] = contact
            entry["group_name"] = contact
        combined = "\n".join(msgs)
        log.info(
            "推送 %d 条消息、%d 个媒体段 [%s|%s]",
            len(msgs), len(media_segments), "群" if is_group else "私", contact,
        )

        # 构建 OneBot 事件（user_id 要用发言人身份，不能用群 sessionId）
        if is_group:
            sender_wxid = entry.get("session_id_data", "") + "_" + (entry.get("sender_in_group", "") or entry.get("source_name", ""))
        else:
            sender_wxid = entry.get("session_id_data", sender_id)
        user_id = state._wxid_to_int(sender_wxid)

        if is_group:
            group_id = state._wxid_to_int(entry.get("session_id_data") or entry.get("group_name", contact))
            sender_name = entry.get("sender_in_group", "") or entry.get("source_name", "未知")

            if state.group_reply_mode == "batch":
                # 批处理模式：消息已预格式化好，直接使用
                formatted = combined
            else:
                # 去掉消息中的 @机器人 纯文本，换为 OneBot at 元素
                clean_text = combined
                for nick in config.BOT_NICKNAMES:
                    for at_pattern in (f"@{nick}", f"＠{nick}"):
                        if at_pattern in clean_text:
                            clean_text = clean_text.replace(at_pattern, "").strip()

                formatted = clean_text

            # Preserve user intent: ordinary messages must not wake the native Agent.
            mentioned = any(self._is_mentioned({"content": text}) for text in msgs)
            if mentioned:
                msg_segments = [{"type": "at", "data": {"qq": str(state._self_id_int)}}]
                if formatted:
                    msg_segments.append({"type": "text", "data": {"text": f" {formatted}"}})
            else:
                msg_segments = []
                if formatted:
                    msg_segments.append({"type": "text", "data": {"text": formatted}})
            msg_segments.extend(media_segments)
            event = make_message_event("group", user_id, msg_segments,
                                       group_id=group_id,
                                       group_name=entry.get("group_name", contact),
                                       nickname=sender_name)
            # Original mention evidence remains available to existing plugins.
            event["wxbridge"] = {
                "synthetic_wakeup": False,
                "mentioned": mentioned,
                "mention_source": "nickname_text",
            }
        else:
            sender_name = entry.get("source_name", contact)
            msg_segments = []
            if combined:
                msg_segments.append({"type": "text", "data": {"text": combined}})
            msg_segments.extend(media_segments)
            event = make_message_event("private", user_id, msg_segments,
                                       nickname=sender_name)

        # 记录 user_id → contact 映射，供 API 回复时查找
        state._contact_to_session[contact] = entry.get("session_id_data", "")
        if is_group:
            group_id = state._wxid_to_int(entry.get("session_id_data") or entry.get("group_name", contact))
            state._ob_id_to_contact[group_id] = contact
        else:
            state._ob_id_to_contact[user_id] = contact

        log.info("[OB11] 入站关联: message_id=%s group_id=%s",
                 event.get("message_id"), event.get("group_id"))
        sent = push_event(event)
        if sent <= 0 and is_group:
            for _ in range(12):
                time.sleep(2.5)
                sent = push_event(event)
                if sent > 0:
                    log.info(f"✅ AstrBot 重连后补推成功 [{contact}]")
                    break
        if sent > 0:
            log.info(f"✅ 已推送至 {sent} 个 AstrBot 客户端 [{contact}]")
        else:
            log.warning(f"⚠️ 无 AstrBot 客户端在线 [{contact}]")

        with self.buffer_lock:
            if sender_id in self.pending_buffers:
                pending = self.pending_buffers[sender_id]
                pending["processing"] = False
                # New arrivals during push/reconnect need their own timer.
                if pending["messages"] or pending.get("segments"):
                    if pending.get("timer"):
                        pending["timer"].cancel()
                    pending["timer_version"] = pending.get("timer_version", 0) + 1
                    next_version = pending["timer_version"]
                    timer = threading.Timer(
                        config.BUFFER_SECONDS,
                        lambda sid=sender_id, v=next_version: self.process_sender(sid, v),
                    )
                    timer.daemon = True
                    pending["timer"] = timer
                    timer.start()

    def listen_sse(self):
        """连接 WeFlow SSE 推送。"""
        sse_url = f"{config.WE_FLOW_BASE_URL}/api/v1/push/messages?access_token={config.ACCESS_TOKEN}"
        log.info("连接 WeFlow 推送服务: %s/api/v1/push/messages", config.WE_FLOW_BASE_URL)
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}

        try:
            self._sse_session = requests.get(sse_url, headers=headers, stream=True, timeout=None)
            if self._sse_session.status_code != 200:
                log.error(f"连接失败: HTTP {self._sse_session.status_code}")
                return
            log.info("✅ 已连接到 WeFlow 推送")

            for line in self._sse_session.iter_lines(decode_unicode=True):
                if not state.running:
                    break
                if not line:
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        data = json.loads(data_str)
                        msg_time = data.get("timestamp", 0)
                        if msg_time < self.start_timestamp:
                            continue
                        raw_id = data.get("rawid", "")
                        if raw_id in self.processed_ids:
                            continue
                        self.processed_ids.add(raw_id)
                        if not self.should_ignore(data):
                            if data.get("sessionType", "") == "group" or "@chatroom" in data.get("sessionId", ""):
                                content = data.get("content", "")
                                log.info(f"📩 群消息 [{data.get('sourceName','')}]: {content[:60]}")
                                if state.group_reply_mode == "mention":
                                    mentioned = any(f"@{n}" in content for n in config.BOT_NICKNAMES)
                                    log.info(f"   @={mentioned}")
                            else:
                                log.info(f"📩 收到: {data.get('sourceName','')} → {data.get('content','')[:50]}")
                            self.add_to_buffer(data)
                    except json.JSONDecodeError:
                        pass

        except requests.exceptions.ConnectionError:
            log.error("无法连接 WeFlow")
        except Exception as e:
            log.error(f"SSE 异常: {e}")
        finally:
            self._sse_session = None

    def _fetch_wechat_image(self, talker: str, message=None) -> str | None:
        """Download only the uniquely identified WeFlow image."""
        message = message if isinstance(message, dict) else {}
        server_id = str(message.get("rawid") or message.get("serverId") or "").strip()
        local_id = str(message.get("localId") or "").strip()
        stamp = message.get("timestamp") or message.get("createTime")
        if not talker or not (server_id or local_id):
            log.warning("图片缺少会话或原消息 ID，拒绝取最新图片代替")
            return None
        try:
            url = f"{config.WE_FLOW_BASE_URL}/api/v1/messages"
            params = {
                "access_token": config.ACCESS_TOKEN,
                "talker": talker,
                "media": "1",
                "image": "1",
                "voice": "0",
                "video": "0",
                "emoji": "0",
                "limit": 100,
            }
            if stamp:
                params.update(start=int(stamp) - 1, end=int(stamp) + 1)
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()

            data = resp.json()
            messages = data if isinstance(data, list) else data.get("messages", data.get("data", []))
            if not isinstance(messages, list):
                messages = []

            def matches_original(item):
                if not isinstance(item, dict):
                    return False
                item_server_id = str(item.get("serverId") or item.get("serverIdRaw") or item.get("rawid") or "").strip()
                item_local_id = str(item.get("localId") or "").strip()
                if server_id and item_server_id != server_id:
                    return False
                if local_id and item_local_id != local_id:
                    return False
                if stamp:
                    item_stamp = item.get("timestamp") or item.get("createTime")
                    if item_stamp is None or int(item_stamp) != int(stamp):
                        return False
                media_type = str(item.get("mediaType") or "").lower()
                local_type = str(item.get("localType") or item.get("type") or "")
                return bool(item.get("mediaUrl")) and (media_type in {"image", "sticker", "emoji"} or local_type == "3")

            matches = [item for item in messages if matches_original(item)]
            if len(matches) != 1:
                log.warning("原图片未唯一匹配或媒体未就绪: server_id=%s local_id=%s matches=%s", server_id, local_id, len(matches))
                return None

            media_url = urljoin(config.WE_FLOW_BASE_URL + "/", matches[0]["mediaUrl"])
            parsed, base = urlsplit(media_url), urlsplit(config.WE_FLOW_BASE_URL)
            if (parsed.scheme, parsed.hostname, parsed.port) != (base.scheme, base.hostname, base.port) or parsed.username or parsed.password:
                raise ValueError("unexpected media origin")

            img_resp = requests.get(media_url, params={"access_token": config.ACCESS_TOKEN}, timeout=30,
                                    stream=True, allow_redirects=False)
            try:
                if img_resp.status_code != 200:
                    raise ValueError("unexpected media response")
                chunks, size = [], 0
                for chunk in img_resp.iter_content(65536):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > _MAX_INLINE_IMAGE_BYTES:
                        raise ValueError("image too large")
                    chunks.append(chunk)
                payload = b"".join(chunks)
            finally:
                close = getattr(img_resp, "close", None)
                if callable(close):
                    close()

            ext = _image_extension(payload)
            if not ext:
                raise ValueError("response is not a supported image")
            save_dir = os.path.join(config.ASTRBOT_ATTACHMENTS or tempfile.gettempdir(), "wechat_images")
            os.makedirs(save_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", prefix="wechat_", suffix=ext, dir=save_dir, delete=False) as handle:
                handle.write(payload)
                save_path = handle.name
            log.info("✅ 微信图片已保存: %s", save_path)
            return save_path
        except Exception as exc:
            log.error("微信图片下载或验证失败: %s", type(exc).__name__)
            return None

    def process_image_message(self, data):
        """处理图片消息：从 WeFlow 取图 → ollama 描述 → 注入缓冲区"""
        session_id = data.get("sessionId", "")
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        rawid = data.get("rawid", "")

        log.info(f"🖼️ 收到图片: {source_name}" +
                 (f" (群:{group_name})" if group_name else ""))

        talker_id = data.get("talkerId", "") or data.get("sessionId", "")
        is_group = bool(group_name) or "@chatroom" in session_id
        sender_in_group = data.get("senderName", "") or data.get("sender", "") or source_name
        if is_group and state.group_reply_mode == "batch" and group_name:
            g_base = re.sub(r'\s*\(\d+\)\s*$', '', group_name).strip()
            buffer_key = f"__batch__{g_base}"
        elif is_group and sender_in_group:
            buffer_key = f"{session_id}_{sender_in_group}"
        else:
            buffer_key = session_id or talker_id

        # 注册待处理的图片（ollama 完成前标记为 pending）
        img_event = threading.Event()
        self._pending_image[buffer_key] = {"caption": None, "event": img_event}

        try:
            # 取图 + ollama 描述
            image_path = self._fetch_wechat_image(session_id, data)
            image_segment = _image_segment_from_path(image_path)
            caption = None
            if image_path:
                caption = caption_image_via_ollama(image_path)

            caption_text = caption if caption else None
            if caption_text:
                log.info(f"📝 图片描述: {caption_text[:60]}...")
            else:
                log.info("⚠️ 图片描述失败")
                caption_text = "（图片内容无法描述）"

            # 注入图片描述到缓冲区
            with self.buffer_lock:
                self._pending_image[buffer_key] = {"caption": caption_text, "event": img_event}

                # 批处理模式用群共享 key
                if is_group and state.group_reply_mode == "batch" and group_name:
                    batch_key = buffer_key
                    if batch_key in self.pending_buffers:
                        entry = self.pending_buffers[batch_key]
                        entry["messages"].insert(0, f'成员"{source_name}"在群"{group_name}"中对你说：[图片: {caption_text}]')
                        if image_segment:
                            entry.setdefault("segments", []).append(image_segment)
                        entry["image_ready"] = True
                        log.info(f"📝 图片已注入批处理队列")
                        return
                    # 没有文字排队，用 batch key 创建独立条目
                    self.pending_buffers[batch_key] = {
                        "messages": [f'成员"{source_name}"在群"{group_name}"中对你说：[图片: {caption_text}]'],
                        "segments": [image_segment] if image_segment else [],
                        "timer": None,
                        "timer_version": 0,
                        "processing": False,
                        "contact": group_name,
                        "is_group": True,
                        "source_name": source_name,
                        "session_id_data": session_id,
                        "group_name": group_name,
                        "sender_in_group": source_name,
                    }
                    log.info(f"📩 图片无文本跟随，创建批处理图片条目")
                    version = 1
                    timer = threading.Timer(5, lambda v=version, sid=batch_key: self.process_sender(sid, v))
                    timer.daemon = True
                    timer.start()
                    self.pending_buffers[batch_key]["timer"] = timer
                    self.pending_buffers[batch_key]["timer_version"] = version
                elif buffer_key in self.pending_buffers:
                    # 已有文本在排队，注入图片上下文
                    entry = self.pending_buffers[buffer_key]
                    entry["messages"].insert(0, f"[图片: {caption_text}]")
                    if image_segment:
                        entry.setdefault("segments", []).append(image_segment)
                    entry["image_ready"] = True
                    log.info(f"📝 图片已注入待处理文本队列")
                    if not entry.get("processing"):
                        if entry.get("timer"):
                            entry["timer"].cancel()
                        entry["timer_version"] = entry.get("timer_version", 0) + 1
                        version = entry["timer_version"]
                        timer = threading.Timer(2, lambda v=version, sid=buffer_key: self.process_sender(sid, v))
                        timer.daemon = True
                        timer.start()
                        entry["timer"] = timer
                else:
                    # 没有文本排队，创建单条图片消息处理
                    log.info(f"📩 图片无文本跟随，直接处理")
                    self.pending_buffers[buffer_key] = {
                        "messages": [f"[图片: {caption_text}]"],
                        "segments": [image_segment] if image_segment else [],
                        "timer": None,
                        "timer_version": 0,
                        "processing": False,
                        "contact": group_name if is_group and group_name else source_name,
                        "is_group": is_group,
                        "source_name": source_name,
                        "session_id_data": session_id,
                        "group_name": group_name if is_group else "",
                        "sender_in_group": "",
                    }
                    version = 1
                    timer = threading.Timer(2, lambda v=version, sid=buffer_key: self.process_sender(sid, v))
                    timer.daemon = True
                    timer.start()
                    self.pending_buffers[buffer_key]["timer"] = timer
                    self.pending_buffers[buffer_key]["timer_version"] = version
        finally:
            # 确保 Event 被设置
            img_event.set()

    def _fetch_wechat_audio(self, talker: str, message=None) -> str | None:
        """Download only the identified WeFlow voice, with bounded same-origin access."""
        raw_id = str((message or {}).get("rawid") or (message or {}).get("serverId") or "").strip()
        if not talker or not raw_id:
            log.warning("语音缺少会话或原消息 ID，拒绝取其他语音代替")
            return None
        try:
            params = {"access_token": config.ACCESS_TOKEN, "talker": talker, "media": "true", "limit": 100}
            stamp = (message or {}).get("timestamp") or (message or {}).get("createTime")
            if stamp:
                params.update(start=int(stamp) - 1, end=int(stamp) + 1)
            resp = requests.get(f"{config.WE_FLOW_BASE_URL}/api/v1/messages", params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            rows = payload if isinstance(payload, list) else payload.get("messages", payload.get("data", []))
            matches = [item for item in rows if isinstance(item, dict)
                       and str(item.get("serverId") or item.get("serverIdRaw") or item.get("rawid") or "") == raw_id
                       and _is_voice_message(item)] if isinstance(rows, list) else []
            if len(matches) != 1 or not matches[0].get("mediaUrl"):
                log.warning("原语音未唯一匹配或媒体未就绪: message_id=%s", raw_id)
                return None
            url = urljoin(config.WE_FLOW_BASE_URL + "/", matches[0]["mediaUrl"])
            parsed, base = urlsplit(url), urlsplit(config.WE_FLOW_BASE_URL)
            if (parsed.scheme, parsed.hostname, parsed.port) != (base.scheme, base.hostname, base.port) or parsed.username or parsed.password:
                raise ValueError("unexpected media origin")
            with requests.get(url, params={"access_token": config.ACCESS_TOKEN}, timeout=30,
                              stream=True, allow_redirects=False) as audio_resp:
                if audio_resp.status_code != 200:
                    raise ValueError("unexpected media response")
                chunks, size = [], 0
                for chunk in audio_resp.iter_content(65536):
                    size += len(chunk)
                    if size > _MAX_INLINE_AUDIO_BYTES:
                        raise ValueError("audio too large")
                    chunks.append(chunk)
            payload = b"".join(chunks)
            # The installed WeFlow exports voice as PCM WAV. Validate bytes, not suffix.
            with wave.open(io.BytesIO(payload), "rb") as wav:
                if wav.getnframes() <= 0 or wav.getframerate() <= 0:
                    raise ValueError("empty audio")
            audio_dir = os.path.join(config.ASTRBOT_ATTACHMENTS or tempfile.gettempdir(), "wechat_audio")
            os.makedirs(audio_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", prefix="wechat_voice_", suffix=".wav", dir=audio_dir, delete=False) as handle:
                handle.write(payload)
                return handle.name
        except Exception as exc:
            log.error("微信语音下载或验证失败: %s", type(exc).__name__)
            return None

    def process_voice_message(self, data):
        """下载微信语音并作为 OneBot record 段转发；失败仍保留可见文本。"""
        session_id = data.get("sessionId", "")
        source_name = data.get("senderName") or data.get("sender") or data.get("sourceName") or "未知"
        group_name = data.get("groupName", "")
        talker_id = f"{session_id}_{source_name}" if "@chatroom" in session_id else session_id
        is_group = bool(group_name) or "@chatroom" in session_id
        audio_path = None
        try:
            audio_path = self._fetch_wechat_audio(session_id, data)
            segment = _audio_segment_from_path(audio_path)
            if segment:
                log.info("语音已转换为 OneBot record 段: %s", source_name)
            else:
                log.warning("语音下载或内联失败，保留 [语音] 文本: %s", source_name)
            self.add_text_to_buffer(talker_id, source_name, group_name, session_id, "" if segment else "[语音下载失败，未获得可识别音频]", is_group, talker_id, media_segment=segment)
        except Exception as exc:
            log.warning("语音处理异常，保留文本: %s", type(exc).__name__)
            self.add_text_to_buffer(talker_id, source_name, group_name, session_id, "[语音]", is_group, talker_id)
        finally:
            if audio_path:
                try:
                    os.unlink(audio_path)
                except OSError:
                    pass

    def process_emoji_message(self, data):
        """处理表情包消息：尝试下载图片并描述，失败则保留原文转发"""
        session_id = data.get("sessionId", "")
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        content = data.get("content", "[表情]")

        log.info(f"😀 收到表情包: {source_name}" +
                 (f" (群:{group_name})" if group_name else ""))

        talker_id = data.get("talkerId", "") or data.get("sessionId", "")
        is_group = bool(group_name) or "@chatroom" in session_id

        # 尝试下载图片描述
        try:
            image_path = self._fetch_wechat_image(session_id, data)
            image_segment = _image_segment_from_path(image_path)
            if image_path:
                if image_segment:
                    image_segment["data"]["sub_type"] = 1
                caption = caption_image_via_ollama(image_path)
                if caption:
                    content = f"[表情: {caption}]"
                    log.info(f"😀 表情包已描述: {caption[:60]}...")
                else:
                    log.info("😀 表情包图片描述失败，保留原文")
            else:
                log.info("😀 表情包无可用图片，保留原文")

            # 直接注入缓冲区（不等待，立即推送）
            self.add_text_to_buffer(talker_id, source_name, group_name,
                                    session_id, content, is_group, talker_id,
                                    image_segment=image_segment)
        except Exception as e:
            log.warning(f"😀 表情包处理异常: {e}")
            # 异常时也保底发送原文
            self.add_text_to_buffer(talker_id, source_name, group_name,
                                    session_id, content, is_group, talker_id)

    def _inject_cached_image(self, session_id, message, buffer_key, version):
        """下载缓存图片 → 描述 → 注入到 buffer 条目（在缓冲计时器到期前完成）"""
        try:
            img_path = self._fetch_wechat_image(session_id, message)
            image_segment = _image_segment_from_path(img_path)
            caption = None
            if img_path:
                caption = caption_image_via_ollama(img_path)
            text = f"[图片: {caption or '无法描述'}]"

            with self.buffer_lock:
                if buffer_key in self.pending_buffers:
                    entry = self.pending_buffers[buffer_key]
                    # 版本匹配才注入（版本变了说明被新消息重置过）
                    if entry.get("timer_version") == version:
                        entry["messages"].insert(0, text)
                        if image_segment:
                            entry.setdefault("segments", []).append(image_segment)
                        log.info(f"📸 缓存图片已注入: {text[:60]}")
                    else:
                        log.info(f"📸 缓存图片跳过（buffer 版本已变更）")
        except Exception as e:
            log.warning(f"📸 缓存图片处理异常: {e}")

    def add_text_to_buffer(self, session_id_data, source_name, group_name,
                           session_id, content, is_group, sender_key,
                           image_segment=None, media_segment=None):
        """通用：将一段文本直接加入缓冲队列（供表情/图片等异步处理完后调用）"""
        with self.buffer_lock:
            buffer_key = sender_key
            if buffer_key not in self.pending_buffers:
                self.pending_buffers[buffer_key] = {
                    "messages": [],
                    "timer": None,
                    "timer_version": 0,
                    "processing": False,
                    "contact": group_name if is_group and group_name else source_name,
                    "is_group": is_group,
                    "source_name": source_name,
                    "session_id_data": session_id,
                    "group_name": group_name if is_group else "",
                    "sender_in_group": source_name if is_group else "",
                }
            entry = self.pending_buffers[buffer_key]
            if content:
                entry["messages"].append(content)
            if image_segment:
                entry.setdefault("segments", []).append(image_segment)
            if media_segment:
                entry.setdefault("segments", []).append(media_segment)

            if not entry["processing"]:
                if entry["timer"]:
                    entry["timer"].cancel()
                entry["timer_version"] += 1
                version = entry["timer_version"]
                delay = 2  # 表情/图片单独推送，短缓冲
                timer = threading.Timer(delay, lambda v=version, sid=buffer_key: self.process_sender(sid, v))
                timer.daemon = True
                timer.start()
                entry["timer"] = timer
                entry["timer_version"] = version
def caption_image_via_ollama(image_path: str) -> str | None:
    if not getattr(config, "IMAGE_CAPTION_ENABLED", False):
        return None
    """对图片进行文字描述，支持 ollama 和 OpenAI 兼容 API 两种后端。"""
    try:
        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        if state.image_caption_provider == "openai":
            if not state.image_caption_api_base:
                log.warning("⚠️ provider=openai 但未配置 api_base，跳过图片描述")
                return None
            providers = [{"api_base": state.image_caption_api_base,
                          "api_key": state.image_caption_api_key,
                          "model": state.image_caption_model}, *state.image_caption_fallbacks]
            for provider in providers:
                try:
                    resp = requests.post(
                        f"{provider['api_base'].rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {provider['api_key']}",
                                 "Content-Type": "application/json"},
                        json={"model": provider["model"], "messages": [{
                            "role": "user", "content": [
                                {"type": "text", "text": state.image_caption_prompt},
                                {"type": "image_url", "image_url": {
                                    "url": f"data:image/jpeg;base64,{img_b64}"}},
                            ]}], "max_tokens": 300},
                        timeout=30,
                    )
                    resp.raise_for_status()
                    caption = resp.json()["choices"][0]["message"].get("content", "") or ""
                    if caption.strip():
                        log.info("图片描述完成: %s", provider["model"])
                        return caption.strip()
                except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
                    log.warning("图片描述失败: %s (%s)", provider.get("model"), type(exc).__name__)
        else:
            # ollama 原生 API
            resp = requests.post(
                f"{state.ollama_base_url}/api/generate",
                json={
                    "model": state.image_caption_model,
                    "prompt": state.image_caption_prompt,
                    "images": [img_b64],
                    "stream": False,
                },
                timeout=state.ollama_timeout,
            )
            if resp.status_code == 200:
                caption = resp.json().get("response", "").strip()
                if caption:
                    log.info(f"🖼️ 图片描述: {caption[:80]}...")
                    return caption
            else:
                log.warning(f"ollama 返回 HTTP {resp.status_code}: {resp.text[:100]}")

    except requests.Timeout:
        log.warning(f"图片描述超时 (30s)")
    except Exception as e:
        log.warning(f"图片描述失败: {e}")
    return None
