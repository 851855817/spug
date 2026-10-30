# Keep the old routes/UI, key wrapper and seven services; retain the new DB columns.
ARG ROLLBACK_IMAGE=spug-baota:b8677f7
FROM ${ROLLBACK_IMAGE}
RUN test -x /baota-entrypoint.sh \
    && test "$(grep -Fxc 'python3 manage.py updatedb' /entrypoint.sh)" -eq 1 \
    && test "$(grep -c '^\[program:' /etc/supervisord.conf)" -eq 7 \
    && ! grep -q 'runpanelworker' /etc/supervisord.conf \
    && sed -i 's/^python3 manage.py updatedb$/python3 manage.py migrate --noinput/' /entrypoint.sh \
    && test "$(grep -Fxc 'python3 manage.py migrate --noinput' /entrypoint.sh)" -eq 1 \
    && ! grep -Eq 'updatedb|makemigrations' /entrypoint.sh
COPY spug_api/apps/baota/models.py spug_api/apps/baota/views.py spug_api/apps/baota/validation.py /opt/spug/spug_api/apps/baota/
COPY spug_api/apps/baota/inventory/ /opt/spug/spug_api/apps/baota/inventory/
COPY spug_api/apps/baota/resource_monitor/__init__.py spug_api/apps/baota/resource_monitor/models.py /opt/spug/spug_api/apps/baota/resource_monitor/
