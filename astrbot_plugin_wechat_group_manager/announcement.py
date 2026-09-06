from __future__ import annotations

from collections.abc import Iterable


def parse_announcement(
    text: str,
    current_group: str,
    allowed_groups: Iterable[str],
    max_length: int = 1000,
) -> tuple[list[str], str]:
    """Parse an announcement command using stable group IDs only."""
    parts = text.strip().split(None, 3)
    if len(parts) < 4 or parts[0] != "/wx" or parts[1] != "公告":
        raise ValueError("格式：/wx 公告 <稳定群ID|当前|全部> <内容>")
    selector, content = parts[2].strip(), parts[3].strip()
    if not content:
        raise ValueError("公告内容不能为空")
    if len(content) > max(1, max_length):
        raise ValueError(f"公告内容不能超过 {max_length} 个字符")

    allowed = {str(item).strip() for item in allowed_groups if str(item).strip()}
    if selector == "当前":
        targets = [str(current_group).strip()] if current_group.strip() else []
    elif selector == "全部":
        targets = sorted(allowed)
    else:
        targets = list(dict.fromkeys(item.strip() for item in selector.split(",") if item.strip()))
        if not all(item.isdigit() for item in targets):
            raise ValueError("目标必须使用稳定群 ID，或使用 当前/全部")

    if not targets:
        raise ValueError("没有可发送的目标群")
    invalid = [item for item in targets if item not in allowed]
    if invalid:
        raise ValueError(f"目标群不在允许列表：{','.join(invalid)}")
    return targets, content
