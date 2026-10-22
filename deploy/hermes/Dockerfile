FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HERMES_RUNTIME_DIR=/opt/hermes/runtime
WORKDIR /opt/hermes
ADD hermes-source.tar.gz /opt/hermes/
RUN python -m pm.build_env --source /opt/hermes --python /usr/local/bin/python \
    --out /opt/hermes/.venv --no-install-project --sealed
ENV PATH=/opt/hermes/.venv/bin:$PATH PYTHONPATH=/opt/hermes HERMES_HOME=/data/hermes
RUN groupadd --gid 10001 blue && useradd --uid 10001 --gid blue --no-create-home blue \
    && mkdir /data && chown blue:blue /data
COPY service.py worker.py /adapter/
USER 10001:10001
WORKDIR /adapter
CMD ["python", "service.py"]
