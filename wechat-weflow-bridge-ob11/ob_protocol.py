"""
OneBot v11 协议处理模块。

包括：
- make_message_event() — 构造 OneBot 消息事件 JSON
- push_event() — 通过 WebSocket 推送事件给 AstrBot
- _handle_ob_api() — 处理 AstrBot 发来的 API 请求（send_msg 等）
- _extract_text() — 从 OneBot message 段提取纯文本
"""

import asyncio
import base64
import json
import os
import tempfile
import time
import logging

import requests
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

import state
import config

log = logging.getLogger("ob11-bridge")


_GROUP_TEXT_COALESCE_SECONDS = 15.0
_group_text_pending = {}
_group_text_tasks = {}


_MARKDOWN_PARSER = MarkdownIt("commonmark", {"html": False}).enable(
    ["table", "strikethrough"]
)


def _render_inline_markdown(node):
    if node.type in ("text", "code_inline", "html_inline"):
        return node.content
    if node.type in ("softbreak", "hardbreak"):
        return "\n"
    if node.type == "image":
        alt = "".join(_render_inline_markdown(child) for child in node.children)
        alt = alt or node.content or "图片"
        source = str(node.attrs.get("src", "")).strip()
        return f"{alt}（{source}）" if source else alt
    if node.type == "link":
        label = "".join(_render_inline_markdown(child) for child in node.children)
        href = str(node.attrs.get("href", "")).strip()
        return f"{label}（{href}）" if href and href != label else label
    return "".join(_render_inline_markdown(child) for child in node.children)


def _render_list(node, depth=0):
    lines = []
    ordered = node.type == "ordered_list"
    start = int(node.attrs.get("start", 1)) if ordered else 1

    for index, item in enumerate(node.children):
        text_blocks = []
        nested_lists = []
        for child in item.children:
            if child.type in ("bullet_list", "ordered_list"):
                nested_lists.append(child)
            else:
                rendered = _render_markdown_block(child, depth + 1)
                if rendered:
                    text_blocks.append(rendered)

        item_lines = "\n".join(text_blocks).splitlines() or [""]
        marker = f"{start + index}. " if ordered else "• "
        indent = "  " * depth
        continuation = "  " * (depth + 1)
        lines.append(f"{indent}{marker}{item_lines[0]}")
        lines.extend(f"{continuation}{line}" for line in item_lines[1:])
        for nested in nested_lists:
            lines.extend(_render_list(nested, depth + 1).splitlines())
    return "\n".join(lines)


def _render_table(node):
    rows = []
    for section in node.children:
        for row in section.children:
            cells = []
            for cell in row.children:
                value = "".join(
                    _render_inline_markdown(child) for child in cell.children
                ).strip()
                cells.append(value)
            rows.append("｜".join(cells))
    return "\n".join(rows)


def _render_markdown_block(node, depth=0):
    if node.type in ("paragraph", "inline"):
        return "".join(_render_inline_markdown(child) for child in node.children)
    if node.type == "heading":
        text = "".join(_render_inline_markdown(child) for child in node.children)
        return f"【{text.strip()}】"
    if node.type in ("bullet_list", "ordered_list"):
        return _render_list(node, depth)
    if node.type == "blockquote":
        content = "\n".join(
            part
            for child in node.children
            if (part := _render_markdown_block(child, depth))
        )
        return "\n".join(
            f"引用：{line}" if line else "引用：" for line in content.splitlines()
        )
    if node.type in ("fence", "code_block"):
        return node.content.rstrip("\n")
    if node.type == "hr":
        return "────────"
    if node.type == "table":
        return _render_table(node)
    return "\n".join(
        part
        for child in node.children
        if (part := _render_markdown_block(child, depth))
    )


def _format_text_for_wechat(text):
    """Render CommonMark into readable text for the WeChat input box."""
    if not isinstance(text, str) or not text:
        return text

    value = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        root = SyntaxTreeNode(_MARKDOWN_PARSER.parse(value))
    except Exception as exc:
        log.warning("Markdown 转微信纯文本失败，保留原文: %s", type(exc).__name__)
        return value

    output = []
    previous_end = None
    for child in root.children:
        if previous_end is not None and child.map and child.map[0] > previous_end:
            output.append("")
        rendered = _render_markdown_block(child)
        if rendered:
            output.extend(line.rstrip() for line in rendered.splitlines())
        if child.map:
            previous_end = child.map[1]

    while output and not output[-1]:
        output.pop()
    return "\n".join(output)


def _verify_text_delivery(contact, text, since):
    session_id = state._contact_to_session.get(contact)
    if not session_id:
        return False
    for attempt in range(5):
        try:
            response = requests.get(
                f"{config.WE_FLOW_BASE_URL}/api/v1/messages",
                params={"access_token": config.ACCESS_TOKEN, "talker": session_id, "limit": 20},
                timeout=5,
            )
            response.raise_for_status()
            for message in response.json().get("messages", []):
                if (message.get("isSend") == 1
                        and message.get("createTime", 0) >= since - 2
                        and message.get("content", "").strip() == text.strip()):
                    return True
        except (requests.RequestException, ValueError, TypeError):
            pass
        if attempt < 4:
            time.sleep(1)
    return False


def _merge_adjacent_text_segments(message):
    """Merge adjacent text segments into one logical outbound message."""
    normalized = []
    for seg in message if isinstance(message, list) else []:
        if isinstance(seg, dict) and seg.get("type") == "text":
            text_value = str(seg.get("data", {}).get("text", "") or "")
            if normalized and normalized[-1].get("type") == "text":
                normalized[-1]["data"]["text"] += text_value
            else:
                normalized.append({"type": "text", "data": {"text": text_value}})
        else:
            normalized.append(seg)
    return normalized


async def _send_formatted_text(contact, text):
    """Send formatted text with UIA retry and WeFlow readback."""
    bridge = state.bridge_instance
    if bridge:
        now = time.time()
        bridge._sent_recently = {
            value: stamp for value, stamp in bridge._sent_recently.items()
            if now - stamp < 120
        }
        bridge._sent_recently[text] = now

    sent = False
    delivery_error = ""
    for attempt in range(4):
        send_started = time.time()
        try:
            sent = await asyncio.to_thread(state.sender_instance.send_text, contact, text)
            if sent:
                sent = await asyncio.to_thread(_verify_text_delivery, contact, text, send_started)
        except Exception as exc:
            sent = False
            delivery_error = f"微信文字发送异常: {type(exc).__name__}"
            log.error("[OB11] 文字发送异常: %s (%s)", contact, type(exc).__name__)
        if sent:
            break
        retryable = bool(getattr(state.sender_instance, "last_failure_retryable", False))
        if attempt < 3 and retryable:
            log.warning("[OB11] 发送在输入前被打断，等待键鼠空闲后重试 (%d/3): %s", attempt + 1, contact)
            idle = await asyncio.to_thread(state.sender_instance.wait_until_user_idle, 5.0, 90.0)
            if not idle:
                delivery_error = f"等待桌面空闲超时: {contact}"
                log.error("[OB11] 等待桌面空闲超时，停止重试: %s", contact)
                break
            continue
        break

    if sent:
        log.info("[OB11] 微信回读确认文字已发送: %s", contact)
    else:
        if bridge:
            bridge._sent_recently.pop(text, None)
        log.error("[OB11] 文字发送失败: %s%s", contact, f" ({delivery_error})" if delivery_error else "")
    return sent


async def _flush_group_text(contact):
    await asyncio.sleep(_GROUP_TEXT_COALESCE_SECONDS)
    texts = _group_text_pending.pop(contact, [])
    _group_text_tasks.pop(contact, None)
    text = "\n".join(item for item in texts if item)
    if text:
        log.info("[OB11] 合并普通群文字出站: %s (%d 段)", contact, len(texts))
        await _send_formatted_text(contact, text)


async def _queue_group_text(contact, text):
    _group_text_pending.setdefault(contact, []).append(text)
    if contact not in _group_text_tasks:
        _group_text_tasks[contact] = asyncio.create_task(_flush_group_text(contact))


async def _handle_ob_api(data: dict):
    """处理 AstrBot 发来的 API 请求。"""
    action = data.get("action", "")
    params = data.get("params", {})
    echo = data.get("echo", "")
    log.info(f"[OB11] API: {action} echo={echo}")

    verified_send = action == "send_group_msg_verified"

    async def respond(status="ok", retcode=0, response_data=None):
        resp_data = {
            "status": status,
            "retcode": retcode,
            "data": response_data or {},
        }
        if echo:
            resp_data["echo"] = echo
        # If WS is reconnecting, briefly wait before giving up on the response.
        resp_sent = False
        for retry in range(10):
            try:
                if state._ob_ws:
                    await state._ob_ws.send(json.dumps(resp_data, ensure_ascii=False))
                    resp_sent = True
                    log.info(f"[OB11] 已回响应: {action} retcode={retcode}")
                    break
                if retry < 9:
                    await asyncio.sleep(0.5)
            except Exception as e:
                log.warning(f"[OB11] 回响应失败 (重试 {retry}/10): {e}")
                if retry < 9:
                    await asyncio.sleep(0.5)
        if not resp_sent:
            log.warning(f"[OB11] 无法回响应（WS 未连接）: {action}")

    # Normal OneBot sends keep their existing early acknowledgement. The
    # verified action is used by announcements and waits for WeChat readback.
    if not verified_send:
        await respond()

    if action in ("send_msg", "send_private_msg", "send_group_msg", "send_group_msg_verified"):
        delivery_ok = True
        delivery_error = ""
        is_group = action in ("send_group_msg", "send_group_msg_verified") or (
            action == "send_msg" and (
                params.get("message_type") == "group" or "group_id" in params
            )
        )
        target_id = params.get("group_id" if is_group else "user_id", 0)
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            pass
        message = params.get("message", [])
        if verified_send and any(not isinstance(seg, dict) or seg.get("type") not in ("text", "at", "reply") for seg in message):
            await respond("failed", 1200, {"delivery": "failed", "error": "当前送达验证仅支持文字；媒体请求未执行"})
            return
        source_ids = [str(seg.get("data", {}).get("id", ""))
                      for seg in message if isinstance(seg, dict) and seg.get("type") == "reply"]
        log.info("[OB11] 出站关联: target_id=%s source_message_ids=%s echo=%s",
                 target_id, source_ids, echo)
        contact = state._ob_id_to_contact.get(target_id, str(target_id))

        # AstrBot can emit one logical group answer as several independent
        # normal send_group_msg calls. Coalesce text-only calls into one
        # WeChat bubble; verified actions and media keep their old semantics.
        if not verified_send and is_group:
            normalized = _merge_adjacent_text_segments(message)
            if normalized and all(
                isinstance(seg, dict) and seg.get("type") == "text"
                for seg in normalized
            ):
                texts = [
                    _format_text_for_wechat(seg.get("data", {}).get("text", ""))
                    for seg in normalized
                ]
                texts = [text for text in texts if text]
                if texts:
                    await _queue_group_text(contact, "\n".join(texts))
                message = []

        # 逐段处理：文字和图片分别发送；相邻文字已合并为一条消息
        for seg in _merge_adjacent_text_segments(message):
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type", "")
            seg_data = seg.get("data", {})

            if seg_type == "text":
                text = _format_text_for_wechat(seg_data.get("text", ""))
                if text:
                    bridge = state.bridge_instance
                    if bridge:
                        now = time.time()
                        bridge._sent_recently = {
                            value: stamp for value, stamp in bridge._sent_recently.items()
                            if now - stamp < 120
                        }
                        bridge._sent_recently[text] = now
                    sent = False
                    for attempt in range(4):
                        send_started = time.time()
                        try:
                            sent = await asyncio.to_thread(state.sender_instance.send_text, contact, text)
                            if sent:
                                sent = await asyncio.to_thread(_verify_text_delivery, contact, text, send_started)
                        except Exception as exc:
                            sent = False
                            delivery_error = f"微信文字发送异常: {type(exc).__name__}"
                            log.error("[OB11] 文字发送异常: %s (%s)", contact, type(exc).__name__)
                        if sent:
                            break
                        retryable = bool(getattr(state.sender_instance, "last_failure_retryable", False))
                        if attempt < 3 and retryable:
                            log.warning(
                                "[OB11] 发送在输入前被打断，等待键鼠空闲后重试 (%d/3): %s",
                                attempt + 1,
                                contact,
                            )
                            idle = await asyncio.to_thread(
                                state.sender_instance.wait_until_user_idle,
                                5.0,
                                90.0,
                            )
                            if not idle:
                                delivery_error = f"等待桌面空闲超时: {contact}"
                                log.error("[OB11] 等待桌面空闲超时，停止重试: %s", contact)
                                break
                            continue
                        break
                    if sent:
                        log.info(f"[OB11] 微信回读确认文字已发送: {contact}")
                    else:
                        if bridge:
                            bridge._sent_recently.pop(text, None)
                        log.error(f"[OB11] 文字发送失败: {contact}")
                        delivery_ok = False
                        if not delivery_error:
                            delivery_error = f"微信文字送达确认失败: {contact}"

            elif seg_type == "image":
                file_val = seg_data.get("file", "")
                if not file_val:
                    log.error("[OB11] 图片发送失败：缺少文件: %s", contact)
                    continue

                img_path = None
                owns_temp_image = file_val.startswith("base64://")

                # AstrBot 通过 aiocqhttp 发图片时用 base64:// 格式
                if file_val.startswith("base64://"):
                    try:
                        # 解码 + 写文件在线程池执行，避免大图卡死事件循环
                        b64_data = file_val[9:]
                        img_path = await asyncio.to_thread(_decode_base64_image, b64_data)
                        if img_path:
                            log.info(f"[OB11] 图片已解码: {os.path.basename(img_path)}")
                    except Exception as e:
                        log.warning(f"[OB11] base64 图片解码失败: {e}")
                else:
                    # 文件名模式：在附件目录找
                    if config.ASTRBOT_ATTACHMENTS:
                        candidates = [
                            os.path.join(config.ASTRBOT_ATTACHMENTS, file_val),
                            os.path.join(config.ASTRBOT_ATTACHMENTS, "wechat_images", file_val),
                        ]
                        for p in candidates:
                            if os.path.exists(p):
                                img_path = p
                                break
                        if not img_path:
                            log.warning(f"[OB11] 图片文件未找到: {file_val}")

                if img_path:
                    try:
                        submitted = await asyncio.to_thread(state.sender_instance.send_image, contact, img_path)
                        if submitted:
                            log.info("[OB11] 图片按键提交完成，尚未验证送达: %s", contact)
                        else:
                            log.error("[OB11] 图片发送器返回失败: %s", contact)
                    except Exception as exc:
                        log.error("[OB11] 图片发送异常: %s (%s)", contact, type(exc).__name__)
                    finally:
                        if owns_temp_image:
                            try:
                                os.unlink(img_path)
                            except OSError:
                                pass
                else:
                    log.error("[OB11] 图片发送失败：文件不可用或解码失败: %s", contact)

            elif seg_type == "face":
                await asyncio.to_thread(state.sender_instance.send_text, contact, "[表情]")
                log.info(f"[OB11] 表情已发送至 {contact}")

            # 其他类型（record, video 等）忽略

        if verified_send:
            if delivery_ok:
                await respond(response_data={"delivery": "verified"})
            else:
                await respond("failed", 1200, {"delivery": "failed", "error": delivery_error})

    else:
        log.debug(f"[OB11] 未处理 API: {action}")


def _extract_text(message: list) -> str:
    """从 OneBot message 段中提取可发送的文本。"""
    text_parts = []
    for seg in message:
        if isinstance(seg, dict):
            t = seg.get("type", "")
            d = seg.get("data", {})
            if t == "text":
                text_parts.append(d.get("text", ""))
            elif t == "image":
                text_parts.append("[图片]")
            elif t == "face":
                text_parts.append("[表情]")
            elif t == "record":
                text_parts.append("[语音]")
            elif t == "video":
                text_parts.append("[视频]")
            elif t == "reply":
                if d.get("text"):
                    text_parts.append(f'"{d["text"]}"')
            elif t == "at":
                text_parts.append(f"@{d.get('qq', d.get('name', ''))}")
            else:
                # 其他未知类型也尝试提取文本
                text_parts.append(d.get("text", ""))
    return "".join(text_parts).strip()


# ============ OneBot 协议处理 ============


def make_message_event(message_type: str, user_id: int, message: list,
                       group_id: int = 0, group_name: str = "",
                       nickname: str = "") -> dict:
    """构造 OneBot v11 消息事件"""
    event = {
        "time": int(time.time()),
        "self_id": state._self_id_int,
        "post_type": "message",
    }
    if message_type == "group":
        event["message_type"] = "group"
        event["group_id"] = group_id
        event["user_id"] = user_id
        event["message"] = message
        event["raw_message"] = "".join(
            seg.get("data", {}).get("text", "") for seg in message
            if seg.get("type") == "text"
        )
        event["sender"] = {"user_id": user_id, "nickname": nickname or str(user_id)}
        event["group_name"] = group_name or str(group_id)
    else:
        event["message_type"] = "private"
        event["user_id"] = user_id
        event["message"] = message
        event["raw_message"] = "".join(
            seg.get("data", {}).get("text", "") for seg in message
            if seg.get("type") == "text"
        )
        event["sender"] = {"user_id": user_id, "nickname": nickname or str(user_id)}
    return event


def push_event(event: dict) -> bool:
    """通过 WebSocket 客户端连接向 AstrBot 推送事件。"""
    if not state._ob_ws or not state._ob_ws_loop:
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(
            state._ob_ws.send(json.dumps(event, ensure_ascii=False)),
            state._ob_ws_loop,
        )
        future.result(timeout=5)
        return True
    except Exception as e:
        log.warning(f"[OB11] 推送事件失败: {e}")
        return False


def _decode_base64_image(b64_data: str) -> str | None:
    """在线程池中执行：解码 base64 图片并保存为临时文件。"""
    import tempfile
    img_data = base64.b64decode(b64_data)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(img_data)
    tmp.close()
    return tmp.name
