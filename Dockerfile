FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LITTLE_BLUE_DATA_DIR=/data
WORKDIR /app
COPY requirements-core.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-core.txt
COPY little_blue/ ./little_blue/
RUN pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 blue \
    && useradd --uid 10001 --gid blue --no-create-home blue \
    && mkdir /data && chown blue:blue /data
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).read()"
CMD ["python", "-m", "little_blue.service", "--host", "0.0.0.0", "--token-file", "/run/secrets/core_token"]
