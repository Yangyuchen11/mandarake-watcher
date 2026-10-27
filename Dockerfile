FROM python:3.10-slim

WORKDIR /app

# 安装基础依赖与东京/上海时区支持
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Tokyo \
    PYTHONUNBUFFERED=1 \
    IN_DOCKER=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 确保运行时目录存在
RUN mkdir -p /app/data

EXPOSE 5005

VOLUME ["/app/data"]

CMD ["python", "-u", "main.py", "--host", "0.0.0.0", "--port", "5005", "--no-browser"]