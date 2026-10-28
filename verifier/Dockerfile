# Verifier image (plan §2.1, trust boundary §2).
# Build context is the repo root, but ONLY verifier/ and simulators/ are
# copied in — src/autobridge (engine) physically cannot leak into this image
# (P1.W9.1: verifier/app.py imports simulators.common/scenarios, so the sim
# package must be present; container networks still isolate the services).
# Fixtures mount read-only at /fixtures (compose.yaml); only control reaches
# this service, on verifier-net.
#
# AR-2-5 reproducible builds: the base image is digest-pinned, and Python
# dependencies come from verifier/requirements.lock (regenerate with
# `make lockfiles`) installed with --require-hashes, so two builds of the
# same commit resolve byte-identical verifier dependencies.
FROM python:3.12-slim@sha256:6b25cbe6577518889d553cdd5a5da97d259348d8f6e45b527dd731caaba27a07

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

COPY verifier/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY verifier/ ./verifier/
COPY simulators/ ./simulators/

EXPOSE 8000

CMD ["uvicorn", "verifier.app:app", "--host", "0.0.0.0", "--port", "8000"]
