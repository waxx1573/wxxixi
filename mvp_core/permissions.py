"""基于稳定用户标识的三级权限，默认拒绝未知身份。"""

import sqlite3
from enum import Enum


class Role(str, Enum):
    MEMBER = "member"
    GROUP_ADMIN = "group_admin"
    SUPER_ADMIN = "super_admin"


class PermissionService:
    def __init__(self, connection: sqlite3.Connection):
        self.db = connection
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS operators (
                user_id TEXT NOT NULL,
                group_id TEXT,
                role TEXT NOT NULL,
                PRIMARY KEY (user_id, group_id)
            );
            """
        )
        self.db.commit()

    def set_role(self, user_id: str, role: Role, group_id: str | None = None) -> None:
        if not user_id.strip():
            raise ValueError("user_id 不能为空")
        if role == Role.GROUP_ADMIN and not group_id:
            raise ValueError("群管理员必须绑定群组")
        if role == Role.SUPER_ADMIN:
            group_id = None
        self.db.execute(
            "INSERT INTO operators(user_id, group_id, role) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, group_id) DO UPDATE SET role=excluded.role",
            (user_id, group_id, role.value),
        )
        self.db.commit()

    def role_for(self, user_id: str | None, group_id: str | None) -> Role:
        if not user_id:
            return Role.MEMBER
        row = self.db.execute("SELECT role FROM operators WHERE user_id=? AND group_id IS NULL", (user_id,)).fetchone()
        if row and row[0] == Role.SUPER_ADMIN.value:
            return Role.SUPER_ADMIN
        if group_id:
            row = self.db.execute("SELECT role FROM operators WHERE user_id=? AND group_id=?", (user_id, group_id)).fetchone()
            if row and row[0] == Role.GROUP_ADMIN.value:
                return Role.GROUP_ADMIN
        return Role.MEMBER

    def allowed(self, user_id: str | None, group_id: str | None, minimum: Role) -> bool:
        rank = {Role.MEMBER: 0, Role.GROUP_ADMIN: 1, Role.SUPER_ADMIN: 2}
        return rank[self.role_for(user_id, group_id)] >= rank[minimum]
