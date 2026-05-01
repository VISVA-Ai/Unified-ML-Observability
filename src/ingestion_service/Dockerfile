FROM python:3.11-slim
WORKDIR /app
COPY src/ingestion_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/shared/ src/shared/
COPY src/ingestion_service/ src/ingestion_service/
ENV PYTHONPATH=/app/src
CMD ["python", "src/ingestion_service/main.py"]
