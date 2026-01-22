# Workflow Orchestrator

Custom workflow orchestration system for Slack bot automation, replacing n8n with a Python-native solution.

## Features

- **FastAPI** - High-performance async web framework for webhooks
- **Celery + Redis** - Distributed task queue for background processing
- **Slack Integration** - Native webhook handling with bot filtering and rate limiting
- **Multi-bot Support** - Configure multiple Slack bots with different channels and working directories
- **Prometheus Metrics** - Built-in observability with /metrics endpoint
- **Structured Logging** - JSON logging for Kubernetes compatibility

## Quick Start

### Prerequisites

- Python 3.12+
- Redis (for Celery broker/backend)
- uv (Python package manager)

### Development Setup

```bash
# Install dependencies
uv sync

# Run the API server
uv run uvicorn orchestrator.main:app --reload

# Run Celery worker (in another terminal)
uv run celery -A orchestrator.celery_app worker --loglevel=info
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `APP_NAME` | Application name | orchestrator |
| `DEBUG` | Enable debug mode | false |
| `LOG_LEVEL` | Logging level | INFO |
| `CELERY_BROKER_URL` | Redis URL for Celery | redis://localhost:6379/0 |
| `CELERY_RESULT_BACKEND` | Redis URL for results | redis://localhost:6379/0 |
| `SLACK_SIGNING_SECRET` | Slack app signing secret | (required) |
| `SLACK_BOT_TOKEN` | Slack bot OAuth token | (required) |
| `RATE_LIMIT_MAX_REQUESTS` | Max requests per window | 10 |
| `RATE_LIMIT_WINDOW_SECONDS` | Rate limit window | 60 |

### Running Tests

```bash
uv run pytest
```

## Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  Slack Events   │────▶│   FastAPI    │────▶│   Celery    │
│   (Webhooks)    │     │  (Webhook    │     │   Worker    │
└─────────────────┘     │   Handler)   │     └──────┬──────┘
                        └──────────────┘            │
                               │                    │
                        ┌──────▼──────┐     ┌──────▼──────┐
                        │    Redis    │◀───▶│   Claude    │
                        │   (Queue)   │     │    CLI      │
                        └─────────────┘     └─────────────┘
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/health/ready` | GET | Readiness probe |
| `/health/live` | GET | Liveness probe |
| `/metrics` | GET | Prometheus metrics |
| `/webhooks/slack` | POST | Slack event webhook |

## Project Structure

```
src/orchestrator/
├── __init__.py
├── main.py              # FastAPI application
├── config.py            # Settings (pydantic-settings)
├── celery_app.py        # Celery configuration
├── api/
│   ├── health.py        # Health check endpoints
│   ├── metrics.py       # Prometheus metrics
│   └── webhooks/
│       └── slack.py     # Slack webhook handler
├── models/
│   ├── slack.py         # Slack event models
│   └── bot.py           # Bot configuration models
├── tasks/
│   └── base.py          # Base Celery task
├── services/
│   └── slack.py         # Slack API client
└── utils/
    ├── logging.py       # Structured logging
    └── rate_limit.py    # Rate limiting
```

## License

Private - HeadbangTech
