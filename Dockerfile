FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
RUN useradd -m bot && mkdir -p /app/data && chown -R bot /app
USER bot

VOLUME ["/app/data"]
CMD ["python", "bot.py"]
