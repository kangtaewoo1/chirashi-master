# 찌라시 마스터 v6 - 배포 및 설정 가이드

## 📋 프로젝트 구조

```
chirashi-master/
├── chirashi/                 # Flask 백엔드
│   ├── app.py               # 메인 애플리케이션
│   ├── requirements.txt      # Python 의존성
│   ├── data/                # 설정 & 데이터
│   └── .gitignore           # Git 제외 파일
├── src/                      # Cloudflare Worker 프록시 (옵션)
├── wrangler.toml            # Cloudflare Workers 설정
└── .github/
    └── workflows/
        └── deploy.yml       # CI/CD 파이프라인
```

## 🔐 GitHub Secrets 설정

GitHub 저장소 → Settings → Secrets → New repository secret

1. **CLOUDFLARE_API_TOKEN**
   - Cloudflare 대시보드 → Account Settings → API Tokens
   - "Edit Cloudflare Workers" 템플릿으로 토큰 생성

2. **CLOUDFLARE_ACCOUNT_ID**
   - Cloudflare 대시보드 → Account Settings 우측 상단
   - Account ID 복사

## 🚀 배포 방법

### 1. 로컬 테스트
```bash
cd chirashi
pip install -r requirements.txt
python app.py
```

### 2. GitHub에 푸시
```bash
git remote add origin https://github.com/YOUR_USERNAME/chirashi-master.git
git branch -M main
git push -u origin main
```

### 3. Cloudflare에 배포
```bash
wrangler login
wrangler deploy
```

## 📊 2captcha 설정

설정 탭에서 다음을 입력하세요:

- **API 키**: 6efd8b94f26628be7a73c23b3769c83d
- **상태**: 활성화 체크

## ⚙️ 환경 변수

`.env` 파일을 만들고 다음을 추가하세요 (개발 환경용):

```env
FLASK_ENV=development
FLASK_DEBUG=True
CHIRASHI_PASSWORD=your-secure-password
CLOUDFLARE_API_TOKEN=your-token
CLOUDFLARE_ACCOUNT_ID=your-account-id
```

**주의**: `.env` 파일은 `.gitignore`에 추가되어 있습니다 (Git에 업로드 안 됨).

## 🔄 GitHub Actions 자동 배포

푸시할 때마다 자동으로:
1. ✅ 코드 컴파일 검사
2. 🧪 테스트 실행
3. 🚀 Cloudflare에 배포

## 📱 접속 주소

- **개발**: http://localhost:8888
- **Cloudflare Worker**: https://google.twseo.kr (도메인 설정 후)

## 🆘 트러블슈팅

**Wrangler 로그인 오류**
```bash
wrangler login --force-new-token
```

**GitHub 푸시 오류**
```bash
git config --global user.name "Your Name"
git config --global user.email "your-email@example.com"
git push -u origin main
```

**Python 의존성 오류**
```bash
pip install --upgrade pip
pip install -r requirements.txt --force-reinstall
```
