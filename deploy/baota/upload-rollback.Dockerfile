# Roll back the UI/runner while retaining every upload table and stored source.
# The old worker cannot interpret file_upload. Keep it disabled on every rollback.
ARG ROLLBACK_IMAGE=spug-baota:49d6dcc
FROM ${ROLLBACK_IMAGE}
RUN test -x /baota-entrypoint.sh \
    && test "$(grep -Fxc 'python3 manage.py updatedb' /entrypoint.sh)" -eq 1 \
    && test "$(grep -c '^\[program:' /etc/supervisord.conf)" -eq 8 \
    && grep -q '^\[program:spug-panel-worker\]' /etc/supervisord.conf \
    && sed -i 's/^python3 manage.py updatedb$/python3 manage.py migrate --noinput/' /entrypoint.sh \
    && test "$(grep -Fxc 'python3 manage.py migrate --noinput' /entrypoint.sh)" -eq 1 \
    && ! grep -Eq 'updatedb|makemigrations' /entrypoint.sh
RUN python3 -c "from pathlib import Path; p=Path('/etc/supervisord.conf'); text=p.read_text(); marker='[program:spug-panel-worker]'; assert text.count(marker)==1; before,worker=text.split(marker); assert '\n[program:' not in worker; assert worker.count('autostart=true')==1 and worker.count('autorestart=true')==1; worker=worker.replace('autostart=true','autostart=false').replace('autorestart=true','autorestart=false'); p.write_text(before+marker+worker)"
COPY spug_api/apps/baota/models.py /opt/spug/spug_api/apps/baota/models.py
