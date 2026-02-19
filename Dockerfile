FROM python:3.12-slim

WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/server.py api/skill_parser.py ./
COPY skills/ /app/skills/

RUN mkdir -p /app/data

EXPOSE 8402

CMD ["gunicorn", "--bind", "0.0.0.0:8402", "--workers", "2", "--timeout", "30", "server:app"]
