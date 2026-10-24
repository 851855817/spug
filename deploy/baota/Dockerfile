FROM ghcr.io/openspug/spug@sha256:13fe7c547932e9479dc8181648ec257b91a18e941ef3f2687d9aeb79882de90c
ARG SOURCE_REVISION
LABEL org.opencontainers.image.source="https://github.com/851855817/spug" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="4.1.1-baota.2"
COPY spug_api/apps/baota /opt/spug/spug_api/apps/baota
COPY spug_api/apps/setting /opt/spug/spug_api/apps/setting
COPY spug_api/apps/ops /opt/spug/spug_api/apps/ops
COPY spug_api/spug/settings.py spug_api/spug/urls.py /opt/spug/spug_api/spug/
COPY spug_web/build/ /opt/spug/spug_web/build/
COPY deploy/baota/entrypoint.sh /baota-entrypoint.sh
COPY deploy/baota/panel-worker.ini /tmp/panel-worker.ini
COPY deploy/baota/ops-worker.ini /tmp/ops-worker.ini
RUN sed -i 's/\r$//' /baota-entrypoint.sh /tmp/panel-worker.ini \
    && chmod 755 /baota-entrypoint.sh \
    && test -f /etc/supervisord.conf \
    && ! grep -q '^\[program:spug-panel-worker\]' /etc/supervisord.conf \
    && cat /tmp/panel-worker.ini >> /etc/supervisord.conf \
    && sed -i 's/\r$//' /tmp/ops-worker.ini \
    && ! grep -q '^\[program:spug-ops-worker\]' /etc/supervisord.conf \
    && cat /tmp/ops-worker.ini >> /etc/supervisord.conf \
    && rm /tmp/panel-worker.ini /tmp/ops-worker.ini
ENTRYPOINT ["/baota-entrypoint.sh"]
