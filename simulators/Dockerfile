# Simulator image (plan §2.1, trust boundary §2).
# Build context is simulators/ ONLY, so engine and verifier code physically
# cannot leak into this image. Serves sim-atlas or sim-harbor via $APP_MODULE.
# The public API listens on 8000 (vendor-net). The admin fault-injection API
# (W2.2) will listen on 8001, reachable only via admin-net.
#
# AR-2-5 reproducible builds: the base image is digest-pinned, and Python
# dependencies come from requirements.lock (regenerate with `make lockfiles`)
# installed with --require-hashes.
FROM python:3.12-slim@sha256:6b25cbe6577518889d553cdd5a5da97d259348d8f6e45b527dd731caaba27a07

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . ./simulators/

EXPOSE 8000 8001

# APP_MODULE is set per service in compose.yaml, e.g. simulators.atlaspms.app:app
# W2.2 dual listener: public on 8000, admin on 8001 (shared state via file).
CMD ["sh", "-c", "python -m simulators.run"]
