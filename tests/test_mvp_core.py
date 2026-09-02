import sqlite3
from datetime import datetime, timezone

from mvp_core import Decision, DecisionEngine, MessageType, PermissionService, Role, SQLiteStore, UnifiedMessage


def message(**overrides):
    values = dict(conversation_id="room@chatroom", message_id="42", sender_id="wxid_a", sender_name="A", message_type=MessageType.TEXT, content="hello", is_group=True)
    values.update(overrides)
    return UnifiedMessage(created_at=datetime(2026, 9, 1, tzinfo=timezone.utc), **values)


def test_storage_is_idempotent_and_persistent(tmp_path):
    path = tmp_path / "bot.db"
    with SQLiteStore(path) as store:
        assert store.record_message(message()) is True
        assert store.record_message(message()) is False
        assert store.count_messages("room@chatroom") == 1
    with SQLiteStore(path) as store:
        assert store.count_messages() == 1


def test_permissions_are_group_scoped_and_unknown_denied():
    db = sqlite3.connect(":memory:")
    permissions = PermissionService(db)
    permissions.set_role("root", Role.SUPER_ADMIN)
    permissions.set_role("admin", Role.GROUP_ADMIN, "room@chatroom")
    assert permissions.allowed("root", "other@chatroom", Role.GROUP_ADMIN)
    assert permissions.allowed("admin", "room@chatroom", Role.GROUP_ADMIN)
    assert not permissions.allowed("admin", "other@chatroom", Role.GROUP_ADMIN)
    assert not permissions.allowed(None, "room@chatroom", Role.GROUP_ADMIN)


def test_decision_prioritizes_commands_mentions_and_group_noise():
    engine = DecisionEngine(("机器人",))
    assert engine.decide(message(content="/设置")).action == Decision.COMMAND
    assert engine.decide(message(content="hi", mentioned_bot=True)).action == Decision.REPLY
    assert engine.decide(message(content="机器人在吗")).action == Decision.REPLY
    assert engine.decide(message(content="成员互聊")).action == Decision.IGNORE
    assert engine.decide(message(is_group=False, content="你好")).action == Decision.REPLY
