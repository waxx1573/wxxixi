#!/bin/sh
set -eu

compose=/root/astrbot/astrbot.yml
sed -i \
  's|image: astrbot-goofish:4.27.5-playwright-sensevoice|image: astrbot-goofish:4.28.0-beta.1-playwright-sensevoice|' \
  "$compose"
docker compose -f "$compose" config >/dev/null
docker compose -f "$compose" up -d --no-deps astrbot
