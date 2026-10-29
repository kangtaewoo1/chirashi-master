"""Bounded deeper public-page checks and optional anonymous local browser rendering."""
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit,urlunsplit,parse_qs,urlencode
from site_reaudit import clean_url,host,safe_public,view_page
from site_verification_detail import annotate,summarize


def followup_urls(result):
    urls=[]
    for board in result.get('flow_profile',{}).get('write',{}).get('boards',[]):
        if board.get('write_mode')=='existing_item_related':continue
        u=clean_url(board['url']);p=urlsplit(u);q=parse_qs(p.query)
        if p.path.endswith('/write.php') and q.get('bo_table'):
            urls.append(urlunsplit((p.scheme,p.netloc,p.path[:-9]+'board.php',urlencode({'bo_table':q['bo_table'][0]}),'')))
        elif p.path.endswith('/write.html') and q.get('board_no'):
            urls.append(urlunsplit((p.scheme,p.netloc,'/board/list.html',urlencode({'board_no':q['board_no'][0]}),'')))
        urls.append(u)
    for kind in ('signup','login'):
        urls+=result.get('flow_profile',{}).get(kind,{}).get('urls',[])[:2]
    if not urls:urls=[p['url'] for p in result.get('pages',[])][:3]
    return list(dict.fromkeys(clean_url(u) for u in urls if clean_url(u)))[:12]


def board_context(pages):
    contexts={}
    for p in pages:
        if p.get('stage')!='entry':continue
        q=parse_qs(urlsplit(p.get('requested_url',p['url'])).query)
        key=tuple((k,q[k][0]) for k in ('bo_table','board_no','mid','kboard_id') if q.get(k))
        if key:contexts.setdefault(key,[]).extend([p.get('title','')]+p.get('headings',[])[:3])
    from site_reaudit import purpose_assessment
    for p in pages:
        if p.get('stage')!='write':continue
        q=parse_qs(urlsplit(p.get('requested_url',p['url'])).query)
        key=tuple((k,q[k][0]) for k in ('bo_table','board_no','mid','kboard_id') if q.get(k))
        p['board_context_titles']=list(dict.fromkeys(contexts.get(key,[])))[:8] if key else []
        if p['board_context_titles']:
            candidate=dict(p,url=p.get('requested_url',p['url']),title=' '.join(p['board_context_titles'])+' '+p.get('title',''),form_state='measured')
            p['purpose_hint']=purpose_assessment({'pages':[candidate]})
    return pages


def render_public(entry,pages,heartbeat=lambda **kw:None,limit=2):
    """One fresh anonymous browser, at most two public navigation targets. No clicks/credentials."""
    driver_path=os.environ.get('SITE_VERIFY_CHROMEDRIVER','/opt/chirashi/runtime/chromedriver')
    if not Path(driver_path).is_file():return {'state':'browser_runtime_unavailable','pages':[],'authenticated':False}
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import UnexpectedAlertPresentException,TimeoutException
    targets=[]
    writes=[p for p in pages if p.get('stage')=='write' and p.get('write_mode')!='existing_item_related']
    if writes:targets.append((writes[0].get('requested_url',writes[0]['url']),'write'))
    signup=next((p for p in pages if p.get('stage')=='signup'),None)
    if signup:targets.append((signup['url'],'signup'))
    if not targets:targets=[(entry['urls'][0],'entry')]
    results=[];driver=None
    with tempfile.TemporaryDirectory(prefix='site-verify-browser-') as temp:
        try:
            options=webdriver.ChromeOptions();options.binary_location='/usr/bin/google-chrome'
            for option in ('--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-gpu',
                           '--no-first-run','--disable-extensions','--disable-background-networking','--lang=ko-KR','--window-size=1280,900'):
                options.add_argument(option)
            options.add_argument('--user-data-dir='+temp);options.add_argument('--disk-cache-dir='+temp+'/cache')
            options.page_load_strategy='eager';options.set_capability('acceptInsecureCerts',False)
            options.set_capability('unhandledPromptBehavior','ignore')
            driver=webdriver.Chrome(service=Service(driver_path,log_output=os.devnull),options=options)
            driver.set_page_load_timeout(14)
            for url,stage in targets[:limit]:
                safe_public(url);heartbeat(domain=entry['domain'],url=url,phase='anonymous_browser',pages=len(results))
                alert_login=False;alert_seen=False;names=()
                try:driver.get(url)
                except UnexpectedAlertPresentException:
                    a=driver.switch_to.alert;text=a.text;alert_login=bool(re.search(r'로그인|회원만|login',text,re.I));alert_seen=True;a.dismiss()
                except TimeoutException:
                    results.append({'url':url,'requested_url':url,'stage':stage,'state':'browser_timeout'});break
                started=time.monotonic();stable=None;stable_at=started
                while time.monotonic()-started<7:
                    try:
                        names=tuple((e.get_attribute('name') or e.get_attribute('id') or e.tag_name) for e in driver.find_elements(By.CSS_SELECTOR,'input,textarea,select') if e.is_displayed())
                        key=(driver.current_url,names)
                        if key!=stable:stable=key;stable_at=time.monotonic()
                        elif time.monotonic()-stable_at>=1.5:break
                    except UnexpectedAlertPresentException:
                        a=driver.switch_to.alert;text=a.text;alert_login=bool(re.search(r'로그인|회원만|login',text,re.I));alert_seen=True;a.dismiss();break
                    time.sleep(.3)
                current=driver.current_url
                if host(current)!=entry['domain']:
                    results.append({'url':clean_url(current),'requested_url':url,'stage':stage,'state':'browser_origin_changed'});break
                markup=driver.page_source
                if re.search(r'NET::ERR_CERT_|Privacy error|연결이 비공개로 설정되어 있지 않습니다',markup):
                    results.append({'url':url,'stage':stage,'state':'tls_validation_failed'});break
                from form_schema import Parser
                parser=Parser();parser.feed(markup);parser.close()
                page=view_page({'url':clean_url(current),'state':'browser_rendered','html':markup},stage)
                page.update(stage=stage,requested_url=url,rendered_browser_checked=True,anonymous_session=True,
                            headings=[n.text()[:160] for n in parser.nodes if n.tag in ('h1','h2','h3')][:12],
                            visible_field_names=list(names),alert_seen=alert_seen,login_notice_observed=alert_login)
                if stage=='write' and (alert_login or re.search(r'login|signin|signup|register|/join',urlsplit(current).path,re.I)):
                    page['form_state']='login_required'
                page=annotate(page,markup);results.append(page)
                if page['human_check']['scope'] in ('page_access','write') or page.get('access_reason'):break
        except Exception as exc:
            return {'state':'browser_review_required','error_type':type(exc).__name__,'pages':results,'authenticated':False,'credentials_entered':False}
        finally:
            if driver:
                try:driver.quit()
                except Exception:pass
    return {'state':'anonymous_render_completed','pages':results,'authenticated':False,'credentials_entered':False,'submitted_forms':0}


def enrich(result,entry,heartbeat=lambda **kw:None):
    result['pages']=board_context(result['pages'])
    blocked=any(p.get('human_check',{}).get('scope') in ('page_access','write') or p.get('access_reason') or p.get('restricted_membership_hint') for p in result['pages'])
    browser=({'state':'not_attempted_existing_access_or_human_hold','pages':[],'authenticated':False} if blocked else render_public(entry,result['pages'],heartbeat))
    result['browser_check']=browser
    if browser.get('pages'):result['pages']+=browser['pages']
    result['pages']=board_context(result['pages'])
    result.update(summarize(result['pages']))
    result.update(inspection_depth='public_board_context_and_anonymous_browser',batch_id=entry.get('batch_id'),
                  browser_authenticated=False,credentials_entered=False,actual_signup_verified=False,actual_login_verified=False)
    return result
