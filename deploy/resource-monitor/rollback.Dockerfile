# Preserve monitor tables and all existing task workers; hide the new UI/routes.
ARG MONITOR_ROLLBACK_IMAGE=spug-baota:5437d77
FROM ${MONITOR_ROLLBACK_IMAGE}
COPY spug_api/apps/baota/models.py /opt/spug/spug_api/apps/baota/models.py
COPY spug_api/apps/baota/resource_monitor/ /opt/spug/spug_api/apps/baota/resource_monitor/
RUN test "$(grep -Fxc 'python3 manage.py updatedb' /entrypoint.sh)" -eq 1 \
    && sed -i 's/^python3 manage.py updatedb$/python3 manage.py migrate --noinput/' /entrypoint.sh \
    && ! grep -Eq 'updatedb|makemigrations' /entrypoint.sh
