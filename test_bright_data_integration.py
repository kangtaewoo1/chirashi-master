#!/usr/bin/env python3
"""테스트: Bright Data 통합 기능"""

import sys
import os

# 프로젝트 루트에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chirashi.bright_data import BrightDataClient, get_bright_data_proxy, enable_web_unlocker, bright_data_client
from chirashi.cafe24_gate import unattended_block

def test_bright_data_integration():
    """Bright Data 통합 기능 테스트"""
    print("Bright Data 통합 기능 테스트 시작")
    
    # BrightDataClient 인스턴스 생성
    client = BrightDataClient()
    
    # 테스트용 사이트 데이터
    test_sites = [
        {
            'site_url': 'https://example.com',
            'platform': 'cafe24',
            'has_captcha': True,
            'write_access_state': None,
            'unattended_audit': {'state': 'ready'},
            'verified_post_url': 'https://example.com/article/1/1',
            'write_test_status': 'passed'
        },
        {
            'site_url': 'https://test.com',
            'platform': 'cafe24',
            'has_captcha': False,
            'write_access_state': None,
            'unattended_audit': {'state': 'ready'},
            'verified_post_url': 'https://test.com/article/1/1',
            'write_test_status': 'passed'
        },
        {
            'site_url': 'https://admin.com',
            'platform': 'cafe24',
            'has_captcha': False,
            'write_access_state': 'admin_only',
            'unattended_audit': {'state': 'ready'},
            'verified_post_url': 'https://admin.com/article/1/1',
            'write_test_status': 'passed'
        }
    ]
    
    print("1. BrightDataClient.is_available() 테스트")
    try:
        is_available = client.is_available()
        print(f"   Bright Data 사용 가능 여부: {is_available}")
    except Exception as e:
        print(f"   오류 발생: {e}")
    
    print("\n2. get_bright_data_proxy() 테스트")
    try:
        proxy = get_bright_data_proxy()
        print(f"   프록시 정보: {proxy}")
    except Exception as e:
        print(f"   오류 발생: {e}")
        
    print("\n3. enable_web_unlocker() 테스트")
    try:
        enabled = enable_web_unlocker()
        print(f"   Web Unlocker 사용 여부: {enabled}")
    except Exception as e:
        print(f"   오류 발생: {e}")
    
    print("\n4. unattended_block() 함수 테스트")
    for i, site in enumerate(test_sites, 1):
        try:
            result = unattended_block(site)
            print(f"   사이트 {i} 결과: {result}")
        except Exception as e:
            print(f"   사이트 {i} 오류 발생: {e}")

if __name__ == "__main__":
    test_bright_data_integration()