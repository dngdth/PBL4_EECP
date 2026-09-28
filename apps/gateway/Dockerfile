FROM python:3.12-slim

WORKDIR /service
COPY apps/gateway/pyproject.toml /service/apps/gateway/pyproject.toml
COPY apps/gateway/app /service/apps/gateway/app
COPY contracts ./contracts
RUN pip install --no-cache-dir /service/apps/gateway

ENV PYTHONPATH=/service
CMD ["python", "-m", "apps.gateway.app.main"]
