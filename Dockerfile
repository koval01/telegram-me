FROM python:3.12.8-slim AS base

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY ./app /code/app
COPY ./static /code/static

COPY ./start.sh /code/start.sh

EXPOSE $PORT

HEALTHCHECK --interval=30s --timeout=5s \
  CMD curl -fI http://localhost:${PORT}/healthz || exit 1

CMD ["bash", "start.sh"]
