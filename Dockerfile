FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# ✅ 빌드 단계에서는 프록시 무시 (핵심)
RUN env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
    pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
