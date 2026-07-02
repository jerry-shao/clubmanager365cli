# clubmanager365 MCP server, HTTP mode — for Cloud Run / Fly.io / any
# serverless container platform.
#
#   docker build -t cm365-mcp .
#   docker run -p 8080:8080 cm365-mcp
#
# The platform's injected PORT env var is honoured automatically.
FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir ".[mcp]"

ENV CM365_MCP_TRANSPORT=http \
    CM365_MCP_HOST=0.0.0.0 \
    CM365_MCP_STATELESS=1

EXPOSE 8080
CMD ["clubmanager365-mcp"]
