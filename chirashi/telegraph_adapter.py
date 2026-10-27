"""Official Telegraph account and publishing API, with durable one-submit fences."""
import hashlib
import json
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

ORIGIN='https://telegra.ph'
API='https://api.telegra.ph'
FLOW={'origin':ORIGIN,'adapter':'telegraph','routes':{'documentation':ORIGIN+'/api','write':ORIGIN+'/'},
      'fields':[{'name':'access_token','role':'account_api_token','type':'password','required':True},
                {'name':'title','role':'post_title','type':'text','required':True},
                {'name':'content','role':'post_body','type':'textarea','required':True}],
      'limits':{'official_api_origin':API,'title_characters':256,'content_bytes':65536,
                'authentication':'official_account_token','retry_uncertain_submission':False,
                'certificate_verification':True,'general_ad_queue_enabled':False}}
_lock=threading.Lock()


def valid_url(url):
    p=urlsplit(str(url or ''))
    return (p.scheme=='https' and p.netloc=='telegra.ph' and p.path not in ('','/','/api')
            and not p.query and not p.fragment and not p.username and not p.password)


def prepare(title,body):
    title,body=str(title).strip(),str(body).strip()
    if not title or len(title)>256 or not body:raise ValueError('invalid_content')
    nodes=[{'tag':'p','children':[p.strip()]} for p in body.split('\n\n') if p.strip()]
    if len(json.dumps(nodes,ensure_ascii=False).encode())>65536:raise ValueError('content_too_large')
    return title,body,nodes


def block(settings,journal,now=None):
    if settings.get('permission') is not True or settings.get('origin')!=ORIGIN:return 'permission_required'
    if settings.get('maintenance_hold') or settings.get('manual_review_only'):return 'maintenance_hold'
    if settings.get('has_captcha') or settings.get('write_access_state') in (
            'admin_only','human_verification_required','login_required'):return 'access_restricted'
    if journal.get('state') in ('submitting','submitted','uncertain'):return 'previous_submission_requires_review'
    now=now or datetime.now(timezone.utc)
    events=list(settings.get('verified_submissions') or [])
    if journal.get('state')=='verified' and journal.get('submitted_at'):events.append(journal['submitted_at'])
    stamps=[datetime.fromisoformat(x) for x in set(events)]
    if any(x.tzinfo is None for x in stamps):return 'invalid_history'
    daily=int(settings.get('daily_limit',1));interval=int(settings.get('min_interval_minutes',1440))
    if daily<1 or interval<1:return 'invalid_limits'
    kst=timezone(timedelta(hours=9))
    if sum(x.astimezone(kst).date()==now.astimezone(kst).date() for x in stamps)>=daily:return 'daily_limit'
    if stamps and (now-max(stamps)).total_seconds()<interval*60:return 'minimum_interval'
    return ''


def call(method,data,post=None):
    import requests
    if method not in ('createAccount','getAccountInfo','createPage','getPage'):
        raise ValueError('unsupported_api_method')
    try:
        r=(post or requests.post)(API+'/'+method,data=data,timeout=30,allow_redirects=False)
        if r.status_code!=200:raise ValueError('provider_http_'+str(r.status_code))
        value=r.json()
        if value.get('ok') is not True or not isinstance(value.get('result'),dict):
            raise ValueError('provider_rejected')
        return value['result']
    except requests.RequestException:
        # Never print a request object, private token, auth_url, or arbitrary provider error.
        raise ValueError('provider_connection_failed') from None


def verify_public(url,title,body,api_call=call,get=None):
    import requests
    from form_schema import Parser
    if not valid_url(url):raise ValueError('invalid_public_url')
    page=api_call('getPage',{'path':urlsplit(url).path.lstrip('/'),'return_content':'true'})
    def flatten(node):
        if isinstance(node,str):return node
        return ' '.join(flatten(x) for x in node.get('children',[]))
    norm=lambda s:' '.join(s.split())
    returned=' '.join(flatten(x) for x in page.get('content',[]))
    if page.get('title')!=title or norm(returned)!=norm(body):raise ValueError('public_api_mismatch')
    r=(get or requests.get)(url,headers={'User-Agent':'Mozilla/5.0'},timeout=25,allow_redirects=False)
    if r.status_code!=200 or not valid_url(r.url):raise ValueError('public_html_failed')
    r.encoding='utf-8';parser=Parser();parser.feed(r.text);parser.close()
    def within_article(node):
        parent=node.parent
        while parent:
            if parent.tag=='article':return True
            parent=parent.parent
        return False
    headings=[n.text() for n in parser.nodes if n.tag=='h1' and within_article(n)]
    paragraphs=[n.text() for n in parser.nodes if n.tag=='p' and within_article(n)]
    if len(headings)!=1 or norm(headings[0])!=norm(title):raise ValueError('public_title_mismatch')
    if norm(' '.join(paragraphs))!=norm(body):raise ValueError('public_body_mismatch')
    return {'state':'verified','transport':'telegraph_server_api','url':url,'http':200,
            'title':title,'body_chars':len(body),'title_match':True,'body_match':True,
            'public_read_without_authentication':True,'public_api_match':True,
            'checked_at':datetime.now(timezone.utc).isoformat(),
            'body_sha256':hashlib.sha256(body.encode()).hexdigest()}


def publish_once(ctx,title,body):
    from telpan_adapter import private_write
    from form_schema import merge_flow_knowledge
    if not _lock.acquire(blocking=False):return {'ok':False,'reason':'job_in_progress'}
    root=Path(ctx['DATA_DIR']);journal_path=root/'telegraph_submission.json';submitted=False
    load=lambda path:json.loads(path.read_text()) if path.exists() else {}
    try:
        import fcntl
        with (root/'telegraph_publish.lock').open('a') as process_lock:
            fcntl.flock(process_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            settings=load(root/'telegraph_settings.json');journal=load(journal_path)
            reason=block(settings,journal)
            if reason:return {'ok':False,'reason':reason,'submitted_posts':0}
            title,body,nodes=prepare(title,body)
            credential_path=root/'telegraph_account_private.json';credential=load(credential_path)
            signup_path=root/'telegraph_signup.json';signup=load(signup_path)
            if not credential:
                if signup.get('state') in ('requesting','unknown','created'):
                    return {'ok':False,'reason':'signup_result_requires_review','submitted_posts':0}
                private_write(signup_path,{'state':'requesting','requests':1})
                try:
                    account=call('createAccount',{'short_name':'WT Project Notes','author_name':'더블유티'})
                    if not account.get('access_token'):raise ValueError('missing_account_token')
                    credential={'token_enc':ctx['_encrypt_password'](account['access_token'])}
                    private_write(credential_path,credential)
                    private_write(signup_path,{'state':'created','requests':1})
                except Exception:
                    private_write(signup_path,{'state':'unknown','requests':1});raise
            token=ctx['_decrypt_password'](credential['token_enc'])
            if not token:raise ValueError('credential_unavailable')
            account=call('getAccountInfo',{'access_token':token,'fields':json.dumps(['short_name','page_count'])})
            if account.get('short_name')!='WT Project Notes':raise ValueError('account_identity_mismatch')
            path=root/'site_flow_knowledge.json'
            with ctx.get('_json_lock',lambda p:threading.RLock())(path):
                db=load(path);row=merge_flow_knowledge(db.get(ORIGIN),FLOW,{'stage':'signup','state':'created'})
                row=merge_flow_knowledge(row,FLOW,{'stage':'login','state':'verified'});db[ORIGIN]=row;private_write(path,db)
            reason=block(load(root/'telegraph_settings.json'),load(journal_path))
            if reason:return {'ok':False,'reason':reason,'submitted_posts':0}
            journal={'state':'submitting','submitted_at':datetime.now(timezone.utc).isoformat(),
                     'title':title,'body':body,'submissions':1};private_write(journal_path,journal);submitted=True
            page=call('createPage',{'access_token':token,'title':title,'author_name':'더블유티',
                                  'content':json.dumps(nodes,ensure_ascii=False),'return_content':'true'})
            url=page.get('url','')
            if not valid_url(url):raise ValueError('unexpected_page_url')
            journal.update(state='submitted',url=url);private_write(journal_path,journal)
            receipt=verify_public(url,title,body)
            journal.update(state='verified',receipt=receipt);private_write(journal_path,journal)
            settings.setdefault('verified_submissions',[]).append(journal['submitted_at'])
            private_write(root/'telegraph_settings.json',settings)
            with ctx.get('_json_lock',lambda p:threading.RLock())(path):
                db=load(path);db[ORIGIN]=merge_flow_knowledge(db[ORIGIN],FLOW,
                    {'stage':'server_post','state':'verified','receipt':receipt});private_write(path,db)
            return {'ok':True,'submitted_posts':1,'signup_verified':True,'token_authentication_verified':True,'receipt':receipt}
    except Exception as exc:
        if submitted:
            journal.update(state='uncertain',reason='submission_or_public_verification_requires_review');private_write(journal_path,journal)
        return {'ok':False,'submitted_posts':int(submitted),'reason':'submission_requires_review' if submitted else 'account_or_preflight_failed','error_type':type(exc).__name__}
    finally:_lock.release()
