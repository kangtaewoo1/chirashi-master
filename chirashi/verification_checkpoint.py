"""Value-free review coverage and resumable stage plans; never grants publishing permission."""
from site_reaudit import clean_url,now


def plan(entry,result):
    profile=result.get('flow_profile') or {}
    boards=[b for b in profile.get('write',{}).get('boards',[]) if b.get('write_mode')!='existing_item_related']
    writes=list(dict.fromkeys(clean_url(b['url']) for b in boards if clean_url(b.get('url',''))))
    urls=list(dict.fromkeys(clean_url(u) for u in entry.get('urls',[]) if clean_url(u)))
    state=result.get('state') or result.get('category') or 'pending'
    return {'domain':entry['domain'],'updated_at':now(),'state':state,'source_category':result.get('category'),
            'saved_urls':urls,'original_write_urls':writes,'automatic_rejection':False,
            'signup':{'status':profile.get('signup',{}).get('status','not_measured'),'urls':profile.get('signup',{}).get('urls',[]),'submitted':False},
            'login':{'status':profile.get('login',{}).get('status','not_measured'),'urls':profile.get('login',{}).get('urls',[]),'submitted':False},
            'return_to_write':{'urls':writes,'authenticated_recheck_done':False},
            'human_checks':result.get('human_checks',[]),
            'promotion_permission':'not_verified','actual_post':'not_attempted','public_title_body':'not_attempted',
            'source': 'deep_public' if result.get('browser_check') else 'public_navigation' if profile else 'first_audit',
            'next_action':result.get('next_action','공개 화면 조사 후 다음 단계를 결정합니다.')}


def save(c,entry,result,overwrite=True):
    import json
    record=plan(entry,result)
    if overwrite:
        c.execute('INSERT INTO review_checkpoints VALUES(?,?,?) ON CONFLICT(domain) DO UPDATE SET record=excluded.record,updated_at=excluded.updated_at',
                  (entry['domain'],json.dumps(record,ensure_ascii=False),record['updated_at']))
    else:
        c.execute('INSERT OR IGNORE INTO review_checkpoints VALUES(?,?,?)',(entry['domain'],json.dumps(record,ensure_ascii=False),record['updated_at']))
    return record
