FROM python:3.13.5-slim AS base

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY ./app /code/app
COPY ./static /code/static

COPY ./start.sh /code/start.sh

RUN chmod +x /code/start.sh

ENV PORT=8000
EXPOSE $PORT

HEALTHCHECK --interval=30s --timeout=5s \
  CMD curl -fI http://localhost:${PORT}/healthz || exit 1

CMD ["bash", "start.sh"]
