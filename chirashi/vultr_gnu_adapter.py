"""Direct Vultr transport for the measured dnc77 board. One attempt per durable job."""
import hashlib
import json
import os
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import requests
from form_schema import Parser

ORIGIN='https://dnc77.cafe24.com'
SID='d7ac3f32065b'

def text(html):
    p=Parser();p.feed(html);p.close();return ' '.join(p.root.text().split())

def valid_result(url,old_url=''):
    p=urlsplit(url);q=parse_qs(p.query);old=urlsplit(old_url);oq=parse_qs(old.query)
    if p.scheme!='https' or p.netloc!='dnc77.cafe24.com' or p.path!='/bbs/board.php':return False
    if q.get('bo_table')!=['freeboard'] or not q.get('wr_id',[''])[0].isdigit():return False
    post=int(q['wr_id'][0])
    return post>0 and (not oq.get('wr_id',[''])[0].isdigit() or post>int(oq['wr_id'][0]))

def publish(site,title,body,ctx,get=requests.get):
    if (site.get('id')!=SID or site.get('site_url','').rstrip('/')!=ORIGIN
        or site.get('bo_table')!='freeboard' or site.get('publish_executor')!='vultr_server'):
        return False,'무인 발행 보류 — 서버 전용 대상 불일치'
    if (not ctx['is_autopostable'](site) or ctx['publishing_block'](site)
        or ctx['unattended_block'](site) or not ctx['under_daily_limit'](site,ctx['load_config']())
        or not ctx['under_min_interval'](site)[0]):
        return False,'무인 발행 보류 — 기존 권한·사람 확인·대기 조건 유지'
    jid=str(site.get('_maintenance_job_id') or '')
    if not jid or not all(c.isalnum() or c in '-_' for c in jid):
        return False,'무인 발행 보류 — 영속 작업 ID 필요'
    folder=Path(ctx['DATA_DIR'])/'vultr_gnu_jobs';folder.mkdir(exist_ok=True)
    journal=folder/(jid+'.json')
    try:fd=os.open(journal,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:return False,'결과 검증 대기 — 기존 제출 기록 확인 필요, 재제출 금지'
    record={'state':'started','job_id':jid,'executor':'vultr_server','started_at':datetime.now(timezone.utc).isoformat()}
    with os.fdopen(fd,'w') as f:json.dump(record,f);f.flush();os.fsync(f.fileno())
    try:
        from charset_guard import prepare_generated_article
        title,body=prepare_generated_article(title,body,'cp949')
        # The existing measured form path handles normal form tokens and checks challenges.
        local=dict(site,_unattended_job=True,_vultr_direct=True)
        ok,url=ctx['gnuboard_post_http'](local,title,body)
        if ok is not True or not valid_result(str(url),str(site.get('verified_post_url') or '')):
            record.update(state='review',reason='form_or_result_requires_review')
            return False,'결과 검증 대기 — '+str(url)[:180]+'; 재제출 금지'
        r=get(url,headers={'User-Agent':'Mozilla/5.0'},timeout=25,allow_redirects=False)
        r.raise_for_status()
        if r.status_code!=200:raise ValueError('public_read_not_200')
        from charset_guard import form_encoding
        r.encoding=form_encoding(r)
        p=Parser();p.feed(r.text);p.close()
        article=next((n for n in p.nodes if n.attrs.get('id')=='writeContents'),None)
        expected=text(body)
        if not article or not expected or expected not in ' '.join(article.text().split()) or ' '.join(title.split()) not in text(r.text):
            raise ValueError('public_title_or_body_mismatch')
        receipt={'state':'verified','job_id':jid,'executor':'vultr_server','transport':'vultr_direct_https',
                 'url':url,'title_match':True,'body_match':True,'body_sha256':hashlib.sha256(expected.encode()).hexdigest(),
                 'encoding':r.encoding,'checked_at':datetime.now(timezone.utc).isoformat()}
        ctx['set_site_flag'](SID,last_publish_transport='vultr_direct_https',unattended_result=receipt,verified_post_url=url,unattended_audit=dict(site.get('unattended_audit') or {},state='ready',checked_at=receipt['checked_at']))
        if site.get('_maintenance_history_id'):
            ctx['history_update'](site['_maintenance_history_id'],verification_receipt=receipt,executor='vultr_server',execution_node='vultr')
        record.update(state='verified',receipt=receipt)
        return True,url
    except Exception as exc:
        record.update(state='review',reason=type(exc).__name__)
        return False,'결과 검증 대기 — 서버 등록·공개 조회 확인 필요, 재제출 금지'
    finally:
        temp=journal.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False));temp.chmod(0o600);os.replace(temp,journal)
