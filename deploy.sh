#!/bin/bash

# 찌라시 마스터 v6 배포 스크립트
# GitHub + Cloudflare 자동 배포

set -e

echo "🚀 찌라시 마스터 v6 배포 시작"
echo "================================"

# 1. Git 설정 확인
echo "📦 Git 저장소 확인..."
if [ -d ".git" ]; then
  echo "✅ Git 저장소 존재"
  git status
else
  echo "❌ Git 저장소 없음. 초기화 중..."
  git init
  git config user.name "chirashi-master"
  git config user.email "aveydg@gmail.com"
fi

# 2. 의존성 설치
echo ""
echo "📥 의존성 설치..."
npm install
cd chirashi && pip install -r requirements.txt && cd ..

# 3. 문법 검사
echo ""
echo "✅ 문법 검사..."
py -3 -m py_compile chirashi/app.py

# 4. Git 커밋
echo ""
echo "📝 코드 커밋..."
git add -A
git commit -m "chore: update deployment at $(date -u +%Y-%m-%dT%H:%M:%SZ)" || echo "No changes to commit"

# 5. Cloudflare 배포
echo ""
echo "☁️  Cloudflare Workers 배포..."
wrangler deploy --env production

echo ""
echo "✅ 배포 완료!"
echo "================================"
echo "🌐 접속 주소:"
echo "   - 프로덕션: https://google.twseo.kr"
echo "   - 로컬: http://localhost:8888"
