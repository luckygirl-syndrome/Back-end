# AWS 배포 가이드 — ttobaba 백엔드

FastAPI + PostgreSQL + Redis + Selenium + **CUDA GPU** 스택 기준으로 작성.

---

## 0. 사전 준비 체크리스트

- [ ] AWS 계정 생성 및 결제 수단 등록
- [ ] AWS CLI 설치 (`brew install awscli`)
- [ ] IAM 사용자 생성 후 `aws configure` 실행
- [ ] `.env` 파일 내용 확인 (배포 시 직접 전달해야 함)

```bash
aws configure
# AWS Access Key ID: ...
# AWS Secret Access Key: ...
# Default region name: ap-northeast-2   ← 서울 리전 권장
# Default output format: json
```

---

## 1. EC2 인스턴스 생성

### 인스턴스 타입 선택

> **GPU가 필요합니다** (torch + CUDA 사용 중)

| 타입 | GPU | vCPU | RAM | 월 비용(참고) | 추천 여부 |
|------|-----|------|-----|--------------|-----------|
| `g4dn.xlarge` | T4 16GB | 4 | 16GB | ~$150 | ✅ 가장 저렴한 GPU |
| `g4dn.2xlarge` | T4 16GB | 8 | 32GB | ~$300 | 트래픽 많을 때 |
| `t3.large` | ❌ | 2 | 8GB | ~$60 | GPU 없이 CPU만 쓸 때 |

**권장: `g4dn.xlarge`** (GPU 없으면 torch 추론 매우 느림)

### AMI 선택

```
Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)
```
- AWS Marketplace에서 검색
- CUDA, nvidia-docker2가 사전 설치되어 있어 설정 시간 절약

### 스토리지

- 루트 볼륨: **50GB 이상** (Docker 이미지 + 모델 파일)
- `models/` 폴더가 있으면 용량 확인 후 조정

---

## 2. 보안 그룹 설정

EC2 콘솔 → 보안 그룹에서 인바운드 규칙 추가:

| 포트 | 프로토콜 | 소스 | 용도 |
|------|---------|------|------|
| 22 | TCP | 내 IP | SSH |
| 8001 | TCP | 0.0.0.0/0 | FastAPI API |
| 5432 | TCP | 내 IP만 | PostgreSQL (관리용, 선택) |

> **주의:** 5432, 6379는 외부에 열지 말 것

---

## 3. EC2 접속 및 초기 설정

```bash
# 키페어 다운로드 후
chmod 400 your-key.pem
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>
```

### Docker 설치 (AMI에 없는 경우)

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker ubuntu
newgrp docker
```

### nvidia-container-toolkit 확인

```bash
nvidia-smi          # GPU 인식 확인
docker run --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

---

## 4. 코드 배포

### 방법 A — git clone (심플)

```bash
git clone https://github.com/<your-org>/Back-end.git
cd Back-end
```

### 방법 B — ECR (프로덕션 권장)

```bash
# 로컬에서 이미지 빌드 & 푸시
aws ecr create-repository --repository-name ttobaba-backend --region ap-northeast-2

aws ecr get-login-password --region ap-northeast-2 | \
  docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com

docker build -t ttobaba-backend .
docker tag ttobaba-backend:latest <ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com/ttobaba-backend:latest
docker push <ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com/ttobaba-backend:latest
```

---

## 5. 환경 변수 설정

EC2 서버에서:

```bash
cd Back-end
cp .env.example .env   # 없으면 직접 생성
nano .env
```

최소 필요 항목:

```env
POSTGRES_USER=ttobaba
POSTGRES_PASSWORD=<강력한_비밀번호>
POSTGRES_DB=ttobaba_db

REDIS_HOST=redis
REDIS_PORT=6379

SECRET_KEY=<JWT_시크릿>
GOOGLE_API_KEY=<Gemini_키>
```

> `.env` 파일은 절대 git에 커밋하지 말 것 (`.gitignore` 확인)

---

## 6. docker-compose 실행

```bash
# GPU 지원 확인 후 실행
docker compose up -d

# 로그 확인
docker compose logs -f app

# 상태 확인
docker compose ps
```

### GPU 설정 문제 시

`docker-compose.yml`의 `deploy.resources` 블록은 Docker Compose v2 + nvidia-container-toolkit 필요.
오류 발생 시:

```bash
# nvidia-container-toolkit 설치
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

---

## 7. 헬스 체크

```bash
curl http://localhost:8001/api/health
# 또는 외부에서
curl http://<EC2_PUBLIC_IP>:8001/api/health
```

---

## 8. (선택) 도메인 + HTTPS

1. **Route 53** 또는 외부 DNS에서 도메인 연결
2. **Elastic IP** 발급 후 EC2에 연결 (IP 변경 방지)
3. **Nginx + Certbot** 으로 HTTPS:

```bash
sudo apt install nginx certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

Nginx 설정 (`/etc/nginx/sites-available/ttobaba`):

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 9. 비용 절감 팁

- **사용 안 할 때 인스턴스 중지** (중지 시 EC2 요금 없음, EBS만 과금)
- Elastic IP는 인스턴스가 중지된 상태에서 연결 안 하면 과금됨
- **Spot Instance** 사용 시 최대 70% 절감 가능 (중단 위험 있음)

---

## 요약 순서

```
1. AWS 계정 + IAM + CLI 설정
2. EC2 g4dn.xlarge 생성 (Deep Learning AMI)
3. 보안 그룹 포트 열기 (22, 8001)
4. SSH 접속 후 git clone
5. .env 파일 작성
6. docker compose up -d
7. curl로 헬스 체크
```
