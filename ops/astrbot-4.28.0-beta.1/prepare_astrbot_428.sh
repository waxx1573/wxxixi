#!/bin/sh
set -eu

backup=/root/astrbot/backups/pre-4.28.0-beta.1-20260906
mkdir -p "$backup"
chmod 700 "$backup"
cp /root/astrbot/astrbot.yml /root/astrbot/Dockerfile.astrbot-goofish "$backup/"
tar -C /root/astrbot/data -czf "$backup/plugins-and-config.tgz" \
  plugins config astrbot_plugin_smart_core.json plugins.json
python3 /root/astrbot/backup_astrbot_db.py
docker pull soulter/astrbot:v4.28.0-beta.1
docker image inspect soulter/astrbot:v4.28.0-beta.1 --format '{{.Id}}'
