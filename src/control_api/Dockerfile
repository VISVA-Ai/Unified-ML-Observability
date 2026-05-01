FROM python:3.11-slim
WORKDIR /app
COPY src/control_api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/shared/ src/shared/
COPY src/control_api/ src/control_api/
ENV PYTHONPATH=/app/src
CMD ["uvicorn", "control_api.main:app", "--host", "0.0.0.0", "--port", "8001"]
