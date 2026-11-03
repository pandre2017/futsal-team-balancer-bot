FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BOT_MODE=webhook
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home appuser
COPY team-balancer-bot.py cloud_run.py ./
USER appuser
CMD ["python", "team-balancer-bot.py"]
