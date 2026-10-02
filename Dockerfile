FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TZ=Asia/Seoul GEVENT_CACHE_DIR=/data
WORKDIR /app
COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --no-deps --require-hashes -r /tmp/requirements.lock && python -m pip check
COPY mime.types /etc/mime.types
COPY --chown=1000:1000 source/ /app/
RUN mkdir -p /data && chown 1000:1000 /data
USER 1000:1000
CMD ["gunicorn", "--worker-class", "gevent", "--workers", "2", "--bind", "0.0.0.0:8080", "--worker-tmp-dir", "/tmp", "--access-logfile", "-", "--error-logfile", "-", "wsgi:application"]
