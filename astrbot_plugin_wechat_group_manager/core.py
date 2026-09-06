from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

RUN_LEVELS = {"capture", "shadow", "assisted", "active"}
URL_RE = re.compile(r"https?://([^/\s]+)", re.I)


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str = ""
    reply: str = ""
    category: str = ""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS groups(group_id TEXT PRIMARY KEY,run_level TEXT NOT NULL,reply_mode TEXT NOT NULL,rules TEXT NOT NULL,quiet_start TEXT NOT NULL,quiet_end TEXT NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS keywords(group_id TEXT NOT NULL,keyword TEXT NOT NULL,reply TEXT NOT NULL,PRIMARY KEY(group_id,keyword));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id TEXT NOT NULL,sender_id TEXT NOT NULL,category TEXT NOT NULL,reason TEXT NOT NULL,content_hash TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_at REAL NOT NULL,resolved_at REAL);
        CREATE TABLE IF NOT EXISTS message_window(group_id TEXT NOT NULL,sender_id TEXT NOT NULL,content_hash TEXT NOT NULL,seen_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS message_window_idx ON message_window(group_id,sender_id,seen_at);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id TEXT NOT NULL,actor_id TEXT NOT NULL,action TEXT NOT NULL,result TEXT NOT NULL,detail TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS announcements(id INTEGER PRIMARY KEY AUTOINCREMENT,target_group_id TEXT NOT NULL,actor_id TEXT NOT NULL,content_hash TEXT NOT NULL,result TEXT NOT NULL,detail TEXT NOT NULL,created_at REAL NOT NULL);
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def ensure(self, group_id: str, level: str, mode: str, rules: str, quiet: list[str]) -> dict:
        start, end = (quiet + ["23:00", "08:00"])[:2]
        self.db.execute("INSERT OR IGNORE INTO groups VALUES(?,?,?,?,?,?,?)", (group_id, level, mode, rules, start, end, time.time()))
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM groups WHERE group_id=?", (group_id,)).fetchone())

    def set_state(self, group_id: str, field: str, value: str) -> None:
        if field == "run_level" and value not in RUN_LEVELS:
            raise ValueError("级别必须是 capture、shadow、assisted 或 active")
        if field == "reply_mode" and value not in {"mention", "keyword"}:
            raise ValueError("模式必须是 mention 或 keyword")
        if field not in {"run_level", "reply_mode"}:
            raise ValueError("不支持的状态字段")
        self.db.execute(f"UPDATE groups SET {field}=?,updated_at=? WHERE group_id=?", (value, time.time(), group_id))
        self.db.commit()

    def set_quiet(self, group_id: str, start: str, end: str) -> None:
        clock = r"(?:[01]\d|2[0-3]):[0-5]\d"
        if not re.fullmatch(clock, start) or not re.fullmatch(clock, end):
            raise ValueError("静默时间格式必须为 HH:MM")
        self.db.execute("UPDATE groups SET quiet_start=?,quiet_end=?,updated_at=? WHERE group_id=?", (start, end, time.time(), group_id))
        self.db.commit()

    def set_rules(self, group_id: str, value: str) -> None:
        self.db.execute("UPDATE groups SET rules=?,updated_at=? WHERE group_id=?", (value[:3000], time.time(), group_id))
        self.db.commit()

    def keyword(self, group_id: str, text: str) -> tuple[str, str] | None:
        for row in self.db.execute("SELECT keyword,reply FROM keywords WHERE group_id=? ORDER BY length(keyword) DESC", (group_id,)):
            if row["keyword"].casefold() in text.casefold():
                return row["keyword"], row["reply"]
        return None

    def add_keyword(self, group_id: str, key: str, reply: str) -> None:
        self.db.execute("INSERT INTO keywords VALUES(?,?,?) ON CONFLICT(group_id,keyword) DO UPDATE SET reply=excluded.reply", (group_id, key[:100], reply[:2000]))
        self.db.commit()

    def delete_keyword(self, group_id: str, key: str) -> bool:
        cursor = self.db.execute("DELETE FROM keywords WHERE group_id=? AND keyword=?", (group_id, key))
        self.db.commit()
        return cursor.rowcount > 0

    def count_messages(self, group_id: str, sender_id: str, text: str, seconds: int) -> tuple[int, int]:
        now, cutoff = time.time(), time.time() - max(1, seconds)
        digest = hashlib.sha256(text.strip().casefold().encode()).hexdigest()
        with self.db:
            self.db.execute("DELETE FROM message_window WHERE seen_at<?", (cutoff,))
            self.db.execute("INSERT INTO message_window VALUES(?,?,?,?)", (group_id, sender_id, digest, now))
        row = self.db.execute("SELECT COUNT(*) n,SUM(CASE WHEN content_hash=? THEN 1 ELSE 0 END) r FROM message_window WHERE group_id=? AND sender_id=? AND seen_at>=?", (digest, group_id, sender_id, cutoff)).fetchone()
        return int(row["n"]), int(row["r"] or 0)

    def add_event(self, group_id: str, sender: str, category: str, reason: str, text: str) -> int:
        cursor = self.db.execute("INSERT INTO events(group_id,sender_id,category,reason,content_hash,created_at) VALUES(?,?,?,?,?,?)", (group_id, sender, category, reason[:500], hashlib.sha256(text.encode()).hexdigest(), time.time()))
        self.db.commit()
        return int(cursor.lastrowid)

    def pending(self, group_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT id,sender_id,category,reason FROM events WHERE group_id=? AND status='open' ORDER BY id DESC LIMIT 10", (group_id,))]

    def resolve(self, group_id: str, event_id: int, status: str) -> bool:
        cursor = self.db.execute("UPDATE events SET status=?,resolved_at=? WHERE group_id=? AND id=? AND status='open'", (status, time.time(), group_id, event_id))
        self.db.commit()
        return cursor.rowcount > 0

    def audit(self, group_id: str, actor: str, action: str, result: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO audit(group_id,actor_id,action,result,detail,created_at) VALUES(?,?,?,?,?,?)", (group_id, actor, action, result, detail[:1000], time.time()))
        self.db.commit()

    def announcement_recent(self, actor: str, target_group: str, cooldown_seconds: int) -> bool:
        cutoff = time.time() - max(0, cooldown_seconds)
        row = self.db.execute(
            "SELECT 1 FROM announcements WHERE actor_id=? AND target_group_id=? AND result='accepted' AND created_at>=? LIMIT 1",
            (actor, target_group, cutoff),
        ).fetchone()
        return row is not None

    def record_announcement(self, actor: str, target_group: str, content: str, result: str, detail: str = "") -> None:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.db.execute(
            "INSERT INTO announcements(target_group_id,actor_id,content_hash,result,detail,created_at) VALUES(?,?,?,?,?,?)",
            (target_group, actor, digest, result, detail[:1000], time.time()),
        )
        self.db.commit()


class Engine:
    def __init__(self, store: Store, config: dict) -> None:
        self.store, self.config = store, config

    def evaluate(self, group: str, sender: str, text: str) -> Decision:
        folded = text.casefold()
        for word in [str(item).casefold() for item in self.config.get("blocked_words", []) if str(item).strip()]:
            if word in folded:
                return Decision("review", f"命中规则关键词：{word}", category="blocked_word")
        domains = [str(item).casefold() for item in self.config.get("ad_domains", []) if str(item).strip()]
        for domain in URL_RE.findall(text):
            if any(domain.casefold() == item or domain.casefold().endswith("." + item) for item in domains):
                return Decision("review", f"命中广告域名：{domain}", category="advertising")
        count, repeat = self.store.count_messages(group, sender, text, int(self.config.get("flood_window_seconds", 10)))
        if count > int(self.config.get("max_messages_per_window", 6)) or repeat >= 3:
            return Decision("review", f"疑似刷屏：窗口消息={count}，重复={repeat}", category="flood")
        keyword = self.store.keyword(group, text)
        return Decision("keyword", f"命中关键词：{keyword[0]}", reply=keyword[1]) if keyword else Decision("none")
