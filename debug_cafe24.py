# 실제 문제 상황을 더 깊이 분석해보겠습니다.
import sys
sys.path.append('chirashi')
from cafe24_gate import publishing_block, access_reason

print('=== 실제 문제 분석 ===')

# 1. access_reason 함수가 제대로 작동하는지 확인
test_strings = [
    '관리자에게만 글쓰기 권한이 있습니다.',
    '관리자 전용 게시판입니다.',
    '[admin_only]',
    '관리자 전용\n게시판입니다.',
    '관리자에게만\t글쓰기\n권한이 있습니다.'
]

print('access_reason 함수 테스트:')
for s in test_strings:
    result = access_reason(s)
    print(f'  \"{s}\" -> {result}')

# 2. 실제 문제 상황을 만들어보겠습니다.
print('\n실제 문제 상황 테스트:')

# 관리자 전용 게시판 상태에서 자격 증명이 있는 경우
site1 = {
    'platform': 'cafe24',
    'write_access_state': 'admin_only',
    'login_required': True,
    'mb_id': 'member',
    'mb_pass': 'dummy_password',
    'write_test_status': 'passed',
    'site_url': 'https://example.com/board/product/list.html?board_no=6',
    'verified_post_url': 'https://example.com/article/test/6/42/'
}

result1 = publishing_block(site1)
print(f'관리자 전용 게시판 + 자격 증명: {result1}')

# 일반 게시판 상태
site2 = {
    'platform': 'cafe24',
    'write_access_state': 'normal',
    'login_required': True,
    'mb_id': 'member',
    'mb_pass': 'dummy_password',
    'write_test_status': 'passed',
    'site_url': 'https://example.com/board/product/list.html?board_no=6',
    'verified_post_url': 'https://example.com/article/test/6/42/'
}

result2 = publishing_block(site2)
print(f'일반 게시판 + 자격 증명: {result2}')

# 문제를 찾기 위해 테스트 케이스를 더 만들어보겠습니다.
print('\n=== 추가 테스트 ===')
site3 = {
    'platform': 'cafe24',
    'write_access_state': 'admin_only',
    'login_required': True,
    'mb_id': 'member',
    'mb_pass_enc': 'encrypted_password',  # 암호화된 비밀번호 사용
    'write_test_status': 'passed',
    'site_url': 'https://example.com/board/product/list.html?board_no=6',
    'verified_post_url': 'https://example.com/article/test/6/42/'
}

result3 = publishing_block(site3)
print(f'관리자 전용 게시판 + 암호화된 자격 증명: {result3}')