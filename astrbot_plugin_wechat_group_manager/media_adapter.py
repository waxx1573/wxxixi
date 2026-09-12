import asyncio


async def materialize_wechat_images(event):
    """Resolve inline bridge images before plugins snapshot event-owned files."""
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if not isinstance(raw, dict) or not isinstance(raw.get("wxbridge"), dict):
        return 0
    count = 0
    for image in event.get_messages():
        ref = getattr(image, "file", "")
        convert = getattr(image, "convert_to_file_path", None)
        if not isinstance(ref, str) or not ref.startswith("base64://") or not callable(convert):
            continue
        if len(ref) > 6 * 1024 * 1024:
            continue
        path = await asyncio.wait_for(convert(), timeout=10)
        if path:
            image.file = str(path)
            image.url = str(path)
            count += 1
    return count
