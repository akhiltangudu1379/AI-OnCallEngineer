FROM python:3.12-slim
WORKDIR /runner
COPY sandbox-runner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sandbox-runner/runner_service.py .
COPY repair-target /repair-target
RUN mkdir /tmp/work && chown -R 65532:65532 /tmp/work
USER 65532
ENV TMPDIR=/tmp/work PYTHONDONTWRITEBYTECODE=1
CMD ["uvicorn","runner_service:app","--host","0.0.0.0","--port","9100"]
