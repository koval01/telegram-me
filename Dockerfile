FROM python:3.12.2

ARG app_port=80

ENV EXPOSE_PORT=$app_port

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY ./ /code

EXPOSE $EXPOSE_PORT

HEALTHCHECK --interval=30s --timeout=5s \
  CMD curl -fI http://localhost:${EXPOSE_PORT}/healthz || exit 1

CMD uvicorn main:app --host 0.0.0.0 --port ${EXPOSE_PORT}