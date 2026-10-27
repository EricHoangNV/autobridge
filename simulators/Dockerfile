# Simulator image (plan §2.1, trust boundary §2).
# Build context is simulators/ ONLY, so engine and verifier code physically
# cannot leak into this image. Serves sim-atlas or sim-harbor via $APP_MODULE.
# The public API listens on 8000 (vendor-net). The admin fault-injection API
# (W2.2) will listen on 8001, reachable only via admin-net.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.34" "pyyaml>=6.0"

COPY . ./simulators/

EXPOSE 8000 8001

# APP_MODULE is set per service in compose.yaml, e.g. simulators.atlaspms.app:app
# W2.2 dual listener: public on 8000, admin on 8001 (shared state via file).
CMD ["sh", "-c", "python -m simulators.run"]
