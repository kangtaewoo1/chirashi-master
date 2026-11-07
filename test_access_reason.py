from chirashi.cafe24_gate import access_reason

# 테스트 케이스들
test_cases = [
    '관리자에게만글쓰기권한',
    '관리자만글쓰기', 
    '관리자만작성',
    '관리자에게만작성권한',
    '관리자전용게시판입니다',
    '[admin_only]',
    '일반 게시판',
    '',
    None
]

print('access_reason 테스트 결과:')
for case in test_cases:
    result = access_reason(case)
    print(f'  "{case}" -> {result}')