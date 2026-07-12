# Single image for both Cloud Run services (webhook-service, worker-service).
# Cloud Run's per-service `command`/`args` override (set in Task 10's Terraform)
# selects which FastAPI app runs: the default CMD below points at
# services.webhook_main:app; worker-service overrides args to point at
# services.worker_main:app instead. No env-var-branching entrypoint script —
# Cloud Run already provides this switch for free.

FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

# WORKDIR matches the final stage's path (/app), not /build: uv bakes an
# absolute shebang (#!/<workdir>/.venv/bin/python) into generated wrapper
# scripts like .venv/bin/uvicorn. If the builder's venv path didn't match
# where it's copied to below, that shebang would point at a path that
# doesn't exist in the final image, and the container would fail to start.
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ app/
COPY services/ services/

FROM python:3.12-slim AS final

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
COPY --from=builder /app/services /app/services

ENV PATH="/app/.venv/bin:$PATH"
USER appuser

EXPOSE 8080
ENTRYPOINT ["uvicorn"]
CMD ["services.webhook_main:app", "--host", "0.0.0.0", "--port", "8080"]
