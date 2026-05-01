FROM python:3.11-slim
WORKDIR /app
COPY src/dashboard/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/shared/ src/shared/
COPY src/dashboard/ src/dashboard/
ENV PYTHONPATH=/app/src
CMD ["streamlit", "run", "src/dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
