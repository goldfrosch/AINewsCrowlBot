FROM python:3.11-slim

# 시스템 타임존 데이터 (zoneinfo용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Seoul

WORKDIR /app

# 의존성 먼저 복사 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# SQLite DB 저장 디렉토리 생성
RUN mkdir -p data

CMD ["python", "main.py"]
