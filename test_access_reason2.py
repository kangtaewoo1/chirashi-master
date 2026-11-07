from cafe24_gate import access_reason

# 테스트 케이스들 - 이전에 추가된 문자열들
test_cases = [
    '관리자전용게시판입니다',
    '[admin_only]'
]

print('access_reason 함수 테스트 결과:')
for case in test_cases:
    result = access_reason(case)
    print('  "' + str(case) + '" -> ' + str(result))