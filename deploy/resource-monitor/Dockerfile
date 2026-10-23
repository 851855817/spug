# Frontend is compiled locally. No dependency installs or builds on the server.
ARG MONITOR_BASE_IMAGE=spug-baota:5437d77
FROM ${MONITOR_BASE_IMAGE}
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
COPY spug_api/apps/baota/ /opt/spug/spug_api/apps/baota/
COPY spug_web/build/ /opt/spug/spug_web/build/
