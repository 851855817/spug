#!/bin/bash
# Run only on the named deployment host, with a reviewed, built release directory.
set -Eeuo pipefail
umask 077
revision=${1:?full source revision}
release_dir=${2:?absolute release directory}
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
[[ "$release_dir" == "/opt/spug/releases/monitor-${revision:0:7}" ]]
cd /opt/spug
new_image="spug-baota:${revision:0:7}"
rollback_image="spug-baota:monitor-rollback-${revision:0:7}"
old_running=$(docker inspect spug --format '{{.Config.Image}}')
[ "$old_running" = 'spug-baota:2350559' ]
[ "$(docker image inspect "$new_image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$revision" ]
[ "$(docker inspect spug --format '{{.State.Health.Status}}')" = healthy ]
docker image inspect "$rollback_image" >/dev/null
python3 - <<'PY'
from pathlib import Path
assert Path('docker-compose.yml').read_text().count('image: spug-baota:5437d77') == 1
PY
backup_dir="/opt/spug/backups/$(date +%Y%m%d-%H%M%S)-before-resource-monitor"
install -d -m 700 "$backup_dir"
cp -p docker-compose.yml .env "$backup_dir/"
docker inspect spug --format '{{range .Config.Env}}{{println .}}{{end}}' > "$backup_dir/app.env"
docker exec -i spug python3 - --require-idle < "$release_dir/deploy/resource-monitor/preserve.py" > "$backup_dir/before.json"
stage_tag="monitor-stage-${revision:0:7}"
stage_net="$stage_tag-net"
stage_data="$stage_tag-data"
stage_db_data="$stage_tag-db-data"
stage_db="$stage_tag-db"
stage_app="$stage_tag-app"
production_switched=no
app_stop_started=no
cleanup_stage() {
    docker rm -f "$stage_app" "$stage_db" >/dev/null 2>&1 || true
    docker volume rm "$stage_data" "$stage_db_data" >/dev/null 2>&1 || true
    docker network rm "$stage_net" >/dev/null 2>&1 || true
}
on_error() {
    trap - ERR
    cleanup_stage
    if [ "$production_switched" = yes ]; then
        # New migrations may have run. Preserve their definitions with guarded rollback.
        python3 - "$rollback_image" <<'PY'
from pathlib import Path
import re, sys
p = Path('docker-compose.yml')
text, count = re.subn(r'image: spug-baota:[^\s]+', 'image: ' + sys.argv[1], p.read_text())
assert count == 1
p.write_text(text)
PY
    elif [ "$app_stop_started" = yes ]; then
        cp -p "$backup_dir/docker-compose.yml" docker-compose.yml
    fi
    if [ "$app_stop_started" = yes ]; then
        docker compose up -d --pull never --no-deps spug
    fi
    # The original container may still be running after an early freeze failure.
    docker exec spug supervisorctl -c /etc/supervisord.conf start nginx >/dev/null 2>&1 || true
    echo "Release failed; previous application/guarded rollback restarted. Private backup: $backup_dir"
    exit 1
}
trap on_error ERR
docker exec spug supervisorctl -c /etc/supervisord.conf stop nginx
# Let accepted API requests finish; idle checks include legacy execution queues,
# running job states/PIDs and active producers, not only the Baota task worker.
sleep 5
docker exec -i spug python3 - --require-idle < "$release_dir/deploy/resource-monitor/preserve.py" > "$backup_dir/frozen.json"
app_stop_started=yes
docker compose stop -t 45 spug
echo 'Application paused; creating paired database/data/key backup.'
docker exec spug-db sh -c 'exec mariadb-dump -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" --single-transaction --routines --events "$MARIADB_DATABASE"' | gzip > "$backup_dir/database.sql.gz"
docker run --rm --pull never --network none --memory 128m --cpus 0.5 --pids-limit 32 --entrypoint tar \
  -v spug_spug-data:/data:ro -v "$backup_dir:/backup" "$old_running" -czf /backup/spug-data.tar.gz -C /data .
gzip -t "$backup_dir/database.sql.gz" "$backup_dir/spug-data.tar.gz"
(cd "$backup_dir" && sha256sum database.sql.gz spug-data.tar.gz > SHA256SUMS && sha256sum -c SHA256SUMS)
chmod 600 "$backup_dir"/*
python3 - "$backup_dir" "$stage_db" <<'PY'
from pathlib import Path
import secrets, sys
root = Path(sys.argv[1])
env = dict(line.split('=', 1) for line in (root/'app.env').read_text().splitlines() if '=' in line)
for key in ('MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_DATABASE'):
    assert env.get(key)
db = {'MARIADB_ROOT_PASSWORD': secrets.token_urlsafe(32), 'MARIADB_USER': env['MYSQL_USER'],
      'MARIADB_PASSWORD': env['MYSQL_PASSWORD'], 'MARIADB_DATABASE': env['MYSQL_DATABASE']}
env['MYSQL_HOST'] = sys.argv[2]
for filename, values in [('stage-db.env', db), ('stage-app.env', env)]:
    p = root/filename
    p.write_text(''.join(k+'='+v+'\n' for k, v in values.items()))
    p.chmod(0o600)
PY
docker network create --internal "$stage_net" >/dev/null
docker volume create "$stage_data" >/dev/null
docker volume create "$stage_db_data" >/dev/null
docker run --rm --pull never --network none --memory 128m --cpus 0.5 --pids-limit 32 --entrypoint tar \
  -v "$stage_data:/data" -v "$backup_dir:/backup:ro" "$old_running" -xzf /backup/spug-data.tar.gz -C /data
db_image=$(docker inspect spug-db --format '{{.Config.Image}}')
docker run -d --pull never --name "$stage_db" --network "$stage_net" --memory 256m --memory-swap 512m --cpus 0.5 --pids-limit 128 \
  --env-file "$backup_dir/stage-db.env" -v "$stage_db_data:/var/lib/mysql" "$db_image" >/dev/null
ready=no
for attempt in $(seq 1 45); do
    if docker exec "$stage_db" healthcheck.sh --connect --innodb_initialized >/dev/null 2>&1; then ready=yes; break; fi
    sleep 1
done
[ "$ready" = yes ]
gzip -dc "$backup_dir/database.sql.gz" | docker exec -i "$stage_db" sh -c 'exec mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'
stage_start() {
    docker run -d --pull never --name "$stage_app" --network "$stage_net" --memory 640m --memory-swap 896m --cpus 1 --pids-limit 128 \
      --env-file "$backup_dir/stage-app.env" -v "$stage_data:/data" "$1" >/dev/null
    local ready=no
    for attempt in $(seq 1 50); do
        if docker exec "$stage_app" curl -fsS http://127.0.0.1/ >/dev/null 2>&1 && \
           [ "$(docker exec "$stage_app" supervisorctl -c /etc/supervisord.conf status 2>/dev/null | awk '$2=="RUNNING" {n++} END {print n+0}')" = 8 ]; then ready=yes; break; fi
        sleep 1
    done
    [ "$ready" = yes ]
}
preserve_check() {
    docker exec -i "$1" python3 - --require-idle < "$release_dir/deploy/resource-monitor/preserve.py" > "$backup_dir/$2.json"
    python3 - "$backup_dir/frozen.json" "$backup_dir/$2.json" <<'PY'
import json, sys
assert json.load(open(sys.argv[1])) == json.load(open(sys.argv[2])), 'Existing data/key fingerprints changed'
PY
}
monitor_check() {
    docker exec -i "$stage_app" python3 - "$1" < "$release_dir/deploy/resource-monitor/check.py"
}
echo 'Rehearsing new / guarded rollback / new on a clone without external network.'
stage_start "$new_image"
preserve_check "$stage_app" stage-new
monitor_check empty
monitor_check seed
docker stop -t 45 "$stage_app" >/dev/null
docker rm "$stage_app" >/dev/null
stage_start "$rollback_image"
preserve_check "$stage_app" stage-rollback
monitor_check retained
docker stop -t 45 "$stage_app" >/dev/null
docker rm "$stage_app" >/dev/null
stage_start "$new_image"
preserve_check "$stage_app" stage-new-again
monitor_check retained
monitor_check clear
monitor_check empty
cleanup_stage
echo 'Rehearsal passed; switching production image.'
python3 - "$new_image" <<'PY'
from pathlib import Path
import sys
p = Path('docker-compose.yml')
text = p.read_text()
assert text.count('image: spug-baota:5437d77') == 1
p.write_text(text.replace('image: spug-baota:5437d77', 'image: ' + sys.argv[1]))
PY
docker compose config --quiet
production_switched=yes
docker compose up -d --pull never --no-deps spug
ready=no
for attempt in $(seq 1 60); do
    if [ "$(docker inspect spug --format '{{.State.Health.Status}}')" = healthy ]; then ready=yes; break; fi
    sleep 1
done
[ "$ready" = yes ]
[ "$(docker exec spug supervisorctl -c /etc/supervisord.conf status | awk '$2=="RUNNING" {n++} END {print n+0}')" = 8 ]
preserve_check spug production
docker exec -i spug python3 - empty < "$release_dir/deploy/resource-monitor/check.py"
curl -fsS http://127.0.0.1:8080/ >/dev/null
printf '%s\n' "$backup_dir" > "$release_dir/backup-location.txt"
trap - ERR
echo "Release healthy, 8 services running, existing data/key fingerprints unchanged. Private backup: $backup_dir"
