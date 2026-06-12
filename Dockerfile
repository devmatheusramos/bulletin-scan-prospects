FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn requests urllib3
COPY . .
CMD ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8000"]
