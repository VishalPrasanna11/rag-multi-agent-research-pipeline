from celery.schedules import crontab
from tasks.celery_app import celery_app

celery_app.conf.beat_schedule = {
    "ingest-daily-prices": {
        "task": "tasks.workers.task_ingest_daily_prices",
        "schedule": crontab(hour=18, minute=0),
        "args": [],
    },
    "ingest-earnings-calendar": {
        "task": "tasks.workers.task_ingest_earnings_calendar",
        "schedule": crontab(hour=6, minute=0),
        "args": [],
    },
    "ingest-analyst-estimates": {
        "task": "tasks.workers.task_ingest_analyst_estimates",
        "schedule": crontab(hour=7, minute=0),
        "args": [],
    },
    "poll-sec-filings": {
        "task": "tasks.workers.task_poll_sec_filings",
        "schedule": 60.0,
        "args": [],
    },
}
