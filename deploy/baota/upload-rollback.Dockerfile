# Roll back the UI/runner while retaining every upload table and stored source.
# The old worker cannot interpret file_upload. Keep it disabled on every rollback.
ARG ROLLBACK_IMAGE=spug-baota:49d6dcc
FROM ${ROLLBACK_IMAGE}
RUN test -x /baota-entrypoint.sh \
    && test "$(grep -Fxc 'python3 manage.py updatedb' /entrypoint.sh)" -eq 1 \
    && test "$(grep -c '^\[program:' /etc/supervisord.conf)" -eq 9 \
    && grep -q '^\[program:spug-panel-worker\]' /etc/supervisord.conf \
    && sed -i 's/^python3 manage.py updatedb$/python3 manage.py migrate --noinput/' /entrypoint.sh \
    && test "$(grep -Fxc 'python3 manage.py migrate --noinput' /entrypoint.sh)" -eq 1 \
    && ! grep -Eq 'updatedb|makemigrations' /entrypoint.sh
RUN python3 -c "import configparser; from pathlib import Path; p=Path('/etc/supervisord.conf'); c=configparser.ConfigParser(interpolation=None); c.read(p); names=('program:spug-panel-worker','program:spug-ops-worker'); assert all(c[n].getboolean('autostart') and c[n].getboolean('autorestart') for n in names); [(c.set(n,k,'false')) for n in names for k in ('autostart','autorestart')]; f=p.open('w'); c.write(f); f.close()"
COPY spug_api/apps/baota/models.py /opt/spug/spug_api/apps/baota/models.py
COPY spug_api/apps/baota/inventory/ /opt/spug/spug_api/apps/baota/inventory/
