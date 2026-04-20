FROM python:3.12-slim AS base
RUN pip install --no-cache-dir fastapi uvicorn
RUN mkdir -p /rover/output
COPY base/server.py /rover/server.py
WORKDIR /rover
EXPOSE 8080

FROM base

RUN pip install --no-cache-dir \
    "httpx>=0.27,<1.0" \
    "anthropic>=0.40,<1.0" \
    "networkx>=3.2,<4.0" \
    "pytest>=8.0,<10.0"

COPY . /rover/

# Make scripts executable
RUN chmod +x /rover/run_mapping.sh /rover/run_reporting.sh

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
