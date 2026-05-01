FROM python:3.11-slim
WORKDIR /app
COPY src/drift_worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/shared/ src/shared/
COPY src/drift_worker/ src/drift_worker/
ENV PYTHONPATH=/app/src
CMD ["celery", "-A", "drift_worker.tasks", "worker", "--beat", "--loglevel=info"]
