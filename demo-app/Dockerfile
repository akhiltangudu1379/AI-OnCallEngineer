FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY demo_service.py .
USER 65532
CMD ["uvicorn","demo_service:app","--host","0.0.0.0","--port","9000"]

