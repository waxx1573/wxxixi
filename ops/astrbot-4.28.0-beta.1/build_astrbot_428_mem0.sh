#!/bin/sh
set -eu

root=${ASTRBOT_ROOT:-/root/astrbot}
dockerfile="$root/Dockerfile.astrbot-goofish"
validator="$root/validate_astrbot_428_image.py"
image=astrbot-goofish:4.28.0-playwright-sensevoice-mem0

test -f "$dockerfile"
test -f "$root/runtime-python-requirements.txt"
test -f "$validator"
test -f "$root/verify_astrbot_image_packages.py"
test -f "$root/test_mem0_takeover.py"
test -d "$root/data/plugins/astrbot_plugin_self_learning"
# Only two reviewed build inputs; never include data/, configs or backups.
tar -C "$root" -cf - Dockerfile.astrbot-goofish runtime-python-requirements.txt | docker build --pull=false -f Dockerfile.astrbot-goofish -t "$image" -
docker run --rm --network none -i -e MEM0_TELEMETRY=false --entrypoint python "$image" - < "$validator"
docker run --rm --network none --entrypoint uv "$image" pip check
python3 "$root/verify_astrbot_image_packages.py" "$image"
docker run --rm --network none -i -e MEM0_TELEMETRY=false \
    --mount "type=bind,src=$root/data/plugins/astrbot_plugin_self_learning,dst=/AstrBot/data/plugins/astrbot_plugin_self_learning,readonly" \
    --entrypoint python "$image" - < "$root/test_mem0_takeover.py"
docker image inspect "$image" --format 'image={{.Id}} size={{.Size}}'
