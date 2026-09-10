#!/bin/sh
set -eu

root=${ASTRBOT_ROOT:-/root/astrbot}
compose="$root/astrbot.yml"
old=astrbot-goofish:4.28.0-beta.1-playwright-sensevoice
new=astrbot-goofish:4.28.0-playwright-sensevoice-mem0
# One recoverable runtime image: the old build lacks runtime-installed Mem0.
recovery=astrbot-goofish:pre-mem0-runtime
validator="$root/validate_astrbot_428_image.py"
changed=0

wait_ready() {
    deadline=$1
    while [ "$(date +%s)" -lt "$deadline" ]; do
        code=$(curl --connect-timeout 2 --max-time 3 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:6185/ || true)
        if [ "$code" = 200 ]; then return 0; fi
        sleep 2
    done
    return 1
}

recover() {
    rc=$?
    trap - EXIT HUP INT TERM
    if [ "$changed" -eq 1 ]; then
        echo "switch_failed: restoring verified runtime image" >&2
        sed -i "s|^    image: $new$|    image: $recovery|" "$compose"
        if timeout 75 docker compose -f "$compose" up -d --no-deps --pull never astrbot \
            && wait_ready "$(( $(date +%s) + 45 ))"; then
            echo "rollback_ready=$recovery" >&2
        else
            echo "ROLLBACK_FAILED: operator action required" >&2
        fi
    fi
    exit "$rc"
}

test -f "$compose"
test -f "$validator"
test -f "$root/create_astrbot_recovery.py"
test -f "$root/backups/self-learning-pre-concurrency-20260910.tar.gz"
command -v timeout >/dev/null
docker image inspect "$old" >/dev/null
docker image inspect "$new" >/dev/null
current=$(docker inspect -f '{{.Config.Image}}' astrbot)
if [ "$current" = "$new" ]; then
    docker exec astrbot uv pip check
    curl --fail --max-time 5 -s -o /dev/null http://127.0.0.1:6185/
    echo "already_running=$new"
    exit 0
fi
if [ "$current" != "$old" ] && [ "$current" != "$recovery" ]; then
    echo "refusing unexpected current image: $current" >&2
    exit 1
fi
old=$current
old_count=$(grep -Fxc "    image: $old" "$compose" || true)
[ "$old_count" -eq 1 ] || { echo "unexpected compose image count" >&2; exit 1; }
docker compose -f "$compose" config --quiet
# Verify source/packages before interrupting production.
python3 "$root/verify_astrbot_image_packages.py" "$new"
# No production mounts or network in image validation.
docker run --rm --network none -i -e MEM0_TELEMETRY=false --entrypoint python "$new" - < "$validator"
docker run --rm --network none --entrypoint uv "$new" pip check
# Do not overwrite an existing recovery point without reviewing its owner/state.
if docker image inspect "$recovery" >/dev/null 2>&1; then
    if [ "$current" != "$recovery" ]; then
        echo "existing recovery image requires review before replacement" >&2
        exit 1
    fi
else
    python3 "$root/create_astrbot_recovery.py" "$recovery"
fi
docker run --rm --network none -i -e MEM0_TELEMETRY=false --entrypoint python "$recovery" - < "$validator"
docker run --rm --network none --entrypoint uv "$recovery" pip check
napcat_before=$(docker inspect -f '{{.Id}} {{.State.StartedAt}}' napcat)

trap recover EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
changed=1
deadline=$(( $(date +%s) + 130 ))
sed -i "s|^    image: $old$|    image: $new|" "$compose"
docker compose -f "$compose" config --quiet
timeout 75 docker compose -f "$compose" up -d --no-deps --pull never astrbot
wait_ready "$deadline"
timeout 15 docker exec astrbot uv pip check
timeout 15 docker exec astrbot python -c 'from importlib.metadata import version; assert version("mem0ai")=="1.0.11"; assert version("qdrant-client")=="1.19.0"; assert version("protobuf")=="6.33.6"'
[ "$(docker inspect -f '{{.Config.Image}} {{.State.Running}}' astrbot)" = "$new true" ]
[ "$napcat_before" = "$(docker inspect -f '{{.Id}} {{.State.StartedAt}}' napcat)" ]
changed=0
trap - EXIT HUP INT TERM
docker inspect -f 'image={{.Config.Image}} running={{.State.Running}} restart={{.RestartCount}} oom={{.State.OOMKilled}}' astrbot
echo 'napcat_unchanged=true'
