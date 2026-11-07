import chirashi.cafe24_gate as gate

# 테스트 케이스를 만들어서 관리자 전용 게시판 판단 로직을 확인
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

print('access_reason 함수 테스트:')
for case in test_cases:
    result = gate.access_reason(case)
    print(f'  "{case}" -> {result}')

# publishing_block 함수 테스트
print('\npublishing_block 함수 테스트:')
test_sites = [
    {'platform': 'cafe24', 'write_access_state': 'admin_only'},
    {'platform': 'cafe24', 'write_access_state': 'human_verification_required'},
    {'platform': 'cafe24', 'write_access_state': 'normal'},
    {'platform': 'cafe24', 'login_required': True, 'mb_id': 'testuser', 'mb_pass': 'password'},
    {'platform': 'cafe24', 'login_required': True, 'mb_id': '', 'mb_pass': ''},
    {'platform': 'cafe24', 'write_test_status': 'failed'},
    {'platform': 'cafe24', 'write_test_status': 'passed'},
]

for i, site in enumerate(test_sites):
    result = gate.publishing_block(site)
    print(f'  Site {i+1}: {site} -> {result}')