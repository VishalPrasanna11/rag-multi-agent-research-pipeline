web: cd backend && uvicorn app:app --host 0.0.0.0 --port $PORT
worker: cd backend && celery -A tasks.celery_app worker --loglevel=info -B
