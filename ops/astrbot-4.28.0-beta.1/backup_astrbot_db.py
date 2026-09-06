import sqlite3

source = sqlite3.connect("/root/astrbot/data/data_v4.db")
target = sqlite3.connect(
    "/root/astrbot/backups/pre-4.28.0-beta.1-20260906/data_v4.db"
)
source.backup(target)
target.close()
source.close()
