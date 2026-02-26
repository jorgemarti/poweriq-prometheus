FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY poweriq_exporter/ poweriq_exporter/

EXPOSE 9131
ENTRYPOINT ["python", "-m", "poweriq_exporter.main"]
