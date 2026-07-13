FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p blik/logs kakao/logs pix/logs twint/logs upi/logs logs

EXPOSE 8060

CMD ["python", "ideal_ui.py", "--host", "0.0.0.0", "--port", "8060", "--no-open"]
