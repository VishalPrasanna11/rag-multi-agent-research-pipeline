import ssl
from celery import Celery
from config import settings

broker_url = settings.REDIS_URL
backend_url = settings.REDIS_URL

redis_opts = {}
if settings.REDIS_TLS:
    redis_opts = {"ssl_cert_reqs": ssl.CERT_NONE}

celery_app = Celery(
    "earnings_intel",
    broker=broker_url,
    backend=backend_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    broker_use_ssl=redis_opts if settings.REDIS_TLS else None,
    redis_backend_use_ssl=redis_opts if settings.REDIS_TLS else None,
)

celery_app.autodiscover_tasks(["tasks"])
