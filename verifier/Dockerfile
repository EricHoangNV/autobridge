# Verifier image (plan §2.1, trust boundary §2).
# Build context is verifier/ ONLY, so engine code physically cannot leak
# into this image. Fixtures mount read-only at /fixtures (compose.yaml);
# only control reaches this service, on verifier-net.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.34"

COPY . ./

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
