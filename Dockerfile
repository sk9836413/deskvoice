FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 DESKVOICE_DB=/tmp/deskvoice.db
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY data/kb ./data/kb
COPY data/past_tickets.json ./data/past_tickets.json
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
