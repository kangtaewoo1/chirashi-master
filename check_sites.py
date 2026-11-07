import json
from pathlib import Path

# 현재 설정된 site_goal 값 확인
try:
    cfg = json.loads(Path('chirashi/data/config.json').read_text(encoding='utf-8'))
    print('Current site_goal:', cfg.get('site_goal', 50))
except FileNotFoundError:
    print('Current site_goal: 50 (기본값)')

# 사이트 목록과 발행 가능한 사이트 수 확인
try:
    sites = json.loads(Path('test_sites.json').read_text(encoding='utf-8'))
    print('Total test sites:', len(sites))
except FileNotFoundError:
    try:
        sites = json.loads(Path('chirashi/data/sites.json').read_text(encoding='utf-8'))
        print('Total sites:', len(sites))
    except FileNotFoundError:
        print('No site data found')
        sites = []

# is_autopostable 조건을 만족하는 사이트 수 계산
count = 0
for s in sites:
    if (s.get('permission') and 
        s.get('status') != 'rejected' and 
        str(s.get('verified_post_url') or '').startswith(('http://','https://'))):
        count += 1

print('Publishable sites (is_autopostable):', count)

# 각 사이트별 unattended_block 결과 확인
from chirashi.cafe24_gate import unattended_block

print("\nDetailed analysis:")
for i, site in enumerate(sites):
    result = unattended_block(site)
    print(f"Site {i+1}: {site.get('site_url')} -> {result}")
    
# Bright Data와 Brave Search 사용 가능 여부 확인
from chirashi.bright_data import bright_data_client

print("\nBright Data status:")
print(f"  API Key available: {bool(bright_data_client.api_key)}")
print(f"  Service available: {bright_data_client.is_available()}")
print(f"  Proxy enabled: {bright_data_client.proxy_enabled}")
print(f"  Web Unlocker enabled: {bright_data_client.web_unlocker_enabled}")

# Brave Search 사용 가능 여부 확인
try:
    from chirashi.bright_data import brave_search_query
    print("\nBrave Search status:")
    search_result = brave_search_query("site:test.com", 5)
    print("  Brave Search API is working")
except Exception as e:
    print(f"  Brave Search API error: {e}")