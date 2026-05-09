FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# [GPU 임시 비활성화] LLM 전환 전까지 CPU 버전으로 실행
# 원래 CUDA 설치 명령어 (GPU 인스턴스로 전환 시 아래 주석 해제 후 그 아래 CPU 설치 주석처리)
# RUN pip install --upgrade pip && \
#     grep -v "^torch==" requirements.txt > /tmp/req_no_torch.txt && \
#     pip install -r /tmp/req_no_torch.txt && \
#     pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121
RUN pip install --upgrade pip && \
    grep -v "^torch==" requirements.txt > /tmp/req_no_torch.txt && \
    pip install -r /tmp/req_no_torch.txt && \
    pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu

COPY . .

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]