# syntax=docker/dockerfile:1
FROM denoland/deno:bin-2.5.4@sha256:1245e9856180be858aebeefdff2c3b98dc01e8ea667b48f4fb64647dc56bb61d AS deno
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS dependencies
WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements.txt requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip /opt/venv/bin/pip install -r requirements.txt -c requirements.lock

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS runtime
WORKDIR /code
COPY --from=deno /deno /usr/local/bin/deno
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir /code/downloads && chown app:app /code/downloads
COPY --from=dependencies /opt/venv /opt/venv
COPY app ./app
COPY migrations ./migrations
COPY main.py alembic.ini ./
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
LABEL org.opencontainers.image.source="https://github.com/Partykriss/whisper_bot"
USER 10001:10001
CMD ["python", "main.py"]
