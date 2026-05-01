FROM python:3.11-slim
WORKDIR /app
COPY src/prediction_api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/shared/ src/shared/
COPY src/prediction_api/ src/prediction_api/
ENV PYTHONPATH=/app/src
CMD ["uvicorn", "prediction_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
