# Verifier image (plan §2.1, trust boundary §2).
# Build context is the repo root, but ONLY verifier/ and simulators/ are
# copied in — src/autobridge (engine) physically cannot leak into this image
# (P1.W9.1: verifier/app.py imports simulators.common/scenarios, so the sim
# package must be present; container networks still isolate the services).
# Fixtures mount read-only at /fixtures (compose.yaml); only control reaches
# this service, on verifier-net.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.34" "pydantic>=2.11" "pyyaml>=6.0"

COPY verifier/ ./verifier/
COPY simulators/ ./simulators/

EXPOSE 8000

CMD ["uvicorn", "verifier.app:app", "--host", "0.0.0.0", "--port", "8000"]
