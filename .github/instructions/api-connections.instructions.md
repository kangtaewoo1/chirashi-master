---
applyTo: "**/*.py,**/.env,**/*.json"
---

# API 연결 작업 지침

- 키 값, 비밀번호, 인증 헤더, Browser API 연결 주소를 답변·로그·커밋에 출력하지 않는다.
- 이 PC의 실제 키는 저장소 루트 `.env`에 설정되어 있다. 새 키를 만들거나 예제 키로 바꾸기 전에 기존 설정을 확인한다.
- `chirashi/bright_data.py`는 `.env`의 `BRIGHT_DATA_API_KEY`, `BRIGHT_DATA_SERP_ZONE`, `BRAVE_SEARCH_API_KEY`를 읽는다.
- 운영 앱의 Brave 설정은 `data/config.json`의 `brave_api_key`, Browser API 연결은 `sbr_endpoint`다. 운영 검색용 Bright Data 설정은 별도 비공개 `data/brightdata_serp_private.json`에 있다. `.env`만 바꿨다고 운영 서버 설정이 바뀌었다고 말하지 않는다.
- Bright Data API 키 확인은 인증 헤더를 넣은 `GET https://api.brightdata.com/zone/get_active_zones`로 한다. 401·403·연결 실패를 성공으로 바꾸지 않는다. `/status`, `/proxy/list`, `/unlocker/config`, `/unlocker/solve`를 추측해서 호출하지 않는다.
- 검색용 `site_discovery`는 SERP API, `jump`는 Browser API다. Browser API의 연결용 비밀번호와 HTTP Bearer API 키는 서로 다른 정보다.
- Brave 검색은 공식 GET 요청과 `X-Subscription-Token` 헤더를 사용한다. 402 `USAGE_LIMIT_EXCEEDED`는 사용량 제한이다. 키 교체나 반복 호출로 해결됐다고 말하지 않는다. 키가 없을 때 인증 없이 호출하지 않는다.
- 2026-10-28 점검: 로컬 Bright Data 인증 200, 실제 SERP 검색 200·결과 10건. Brave는 실제 키로 402 사용량 초과. 과거 예제 `example4.com`의 테스트 결과는 운영 사이트 실측이 아니다.
- 검색 결과나 API 연결 성공은 글쓰기 권한을 증명하지 않는다. `unattended_adapter_unverified`, 관리자 전용·로그인·CAPTCHA·일일 한도·최소 간격 조건을 키 문제 해결 목적으로 완화하지 않는다.
- 실제 새 글의 URL에서 제목·본문 일치를 검증한 곳만 집계한다. API 키가 정상이라는 이유만으로 50곳을 확보했다고 말하지 않는다.
- 유료 검색에는 승인된 요청 수와 비용 상한을 적용하고, 호출 전후 사용량을 기록한다.

공식 문서:
- https://docs.brightdata.com/api-reference/account-management-api/Get_active_Zones
- https://api-dashboard.search.brave.com/api-reference/web/search/get
