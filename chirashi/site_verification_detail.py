"""Stage-specific, unauthenticated evidence. Never infers account or post success."""
import re
from urllib.parse import urlsplit,parse_qs,urlunsplit,urlencode
from form_schema import Parser,_hidden,_anc,measure_html
from site_reaudit import purpose_assessment

ANALYSIS_VERSION=3
RECOVERY=re.compile(r'password[_/-]?(?:lost|reset|find)|find[_/-]?(?:id|password)|forgot|비밀번호\s*(?:찾기|재설정)|아이디\s*찾기',re.I)


def recovery_path(url,label=''):
    return bool(RECOVERY.search(urlsplit(url).path+' '+label))


def human_evidence(markup,page,parser=None):
    p=parser
    if p is None:p=Parser();p.feed(markup);p.close()
    signals=[]
    for n in p.nodes:
        a=n.attrs;marker=' '.join(str(a.get(k) or '') for k in ('name','id','class','src'))
        if n.tag=='script' and re.search(r'captcha|turnstile|veritas-hub|challenge-platform',marker,re.I):
            signals.append({'kind':'integration_script','active_control':False});continue
        if n.tag not in ('input','textarea','iframe','img','div','span') or _hidden(n) or 'disabled' in a:continue
        if n.tag=='input' and (a.get('type') or 'text') in ('hidden','submit','button'):continue
        row=_anc(n,'tr') or _anc(n,'label')
        label=row.text() if row else ''
        match=re.search(r'captcha|turnstile|(?:^|\s)wr_key(?:\s|$)',marker,re.I) or re.search(r'SPAM\s*방지|자동등록방지|보안문자',label,re.I)
        if match or 'data-sitekey' in a:
            form=_anc(n,'form')
            forms=[x for x in p.nodes if x.tag=='form']
            targets={f.get('name') for f in (page.get('authentication_fields') or page.get('fields',[])) if f.get('role') in ('title','content','id','password') and f.get('type')!='hidden'}
            in_form={x.attrs.get('name') for x in p.nodes if _anc(x,'form') is form} if form else set()
            belongs=bool(form and (targets.issubset(in_form) if targets else len(forms)==1))
            signals.append({'kind':'input' if n.tag in ('input','textarea') else 'widget_or_image',
                            'active_control':True,'tag':n.tag,'name':a.get('name') or a.get('id') or '',
                            'belongs_to_target_form':belongs})
    text=p.root.text()
    marker_only=bool(re.search(r'veritas-hub|cf-chl-|challenge-platform',markup,re.I))
    notice_pattern=r'사람인지\s*확인|간단한\s*확인이\s*필요|verify you are human'
    notice=bool(re.search(notice_pattern,text,re.I))
    heading=any(n.tag in ('h1','h2') and not _hidden(n) and re.search(notice_pattern,n.text(),re.I) for n in p.nodes)
    title_gate=bool(re.search(r'just a moment|attention required|checking your browser|보안\s*확인|사람인지\s*확인',page.get('title',''),re.I))
    wall=bool((notice and (len(text)<1200 or heading)) or (marker_only and title_gate))
    if marker_only and not wall and not signals:signals.append({'kind':'security_integration_code','active_control':False})
    if recovery_path(page['url']):scope='account_recovery_unrelated'
    elif wall:scope='page_access'
    elif any(x['active_control'] for x in signals):
        actual=page.get('actual_page_role',page['stage'])
        scope=actual if actual in ('write','signup','login') and any(x.get('belongs_to_target_form') for x in signals) else 'comment_or_embedded_unconfirmed'
    elif signals:scope='integration_only_unconfirmed'
    else:scope='not_observed'
    return {'scope':scope,'signals':signals,'page_gate_evidence':{'integration_code':marker_only,'notice_text':notice,'notice_heading':bool(heading),'challenge_title':title_gate,'confirmed':wall},'required_step_confirmed':scope in ('page_access','write','signup','login'),
            'solved':False,'interpretation':'보안 절차의 위치만 기록합니다. 사이트 전체 글쓰기 불가 판정이 아닙니다.'}


def annotate(page,markup):
    page=dict(page);page['stage']='account_recovery' if recovery_path(page['url']) else page['stage']
    page['actual_page_role']='login' if re.search(r'login|signin',urlsplit(page['url']).path,re.I) else page['stage']
    if page['actual_page_role']=='login' and page['stage']=='write':
        auth=measure_html(markup,page['url'],'login');page['authentication_form_state']=auth['state']
        page['authentication_fields']=[{k:f.get(k) for k in ('name','role','type','required','label','minlength','maxlength','selector')} for f in (auth.get('form') or {}).get('fields',[])]
    page['human_signal_original']=page.get('human_verification',False)
    p=Parser();p.feed(markup);p.close();text=p.root.text()
    page['human_check']=human_evidence(markup,page,p)
    page['human_verification']=page['human_check']['required_step_confirmed']
    headings=page.get('headings') or []
    if page['stage']=='write':
        q=parse_qs(urlsplit(page.get('requested_url',page['url'])).query)
        page['write_mode']='existing_item_related' if q.get('wr_id') or q.get('document_srl') else 'new_post_requested'
    if page['stage']=='write' and page.get('form_state')=='measured':
        purpose_page=dict(page,title=page.get('title','')+' '+' '.join(headings[:3]))
        page['purpose_hint']=purpose_assessment({'pages':[purpose_page]})
    if page['stage']=='signup':
        agrees=[n for n in p.nodes if n.tag=='input' and re.search(r'agree|consent',str(n.attrs.get('name') or '')+' '+str(n.attrs.get('id') or ''),re.I)]
        page['terms_gate_observed']=bool(agrees and re.search(r'약관|동의|terms|consent',text,re.I))
        page['terms_accepted']=False
        page['identity_verification_hint']=bool(re.search(r'본인인증|휴대폰.{0,5}인증|아이핀|i-PIN',text,re.I))
        page['email_verification_hint']=bool(re.search(r'이메일.{0,8}인증|메일.{0,8}인증',text))
    return page


def board_key(url):
    p=urlsplit(url);q=parse_qs(p.query)
    routing={k:q[k][0] for k in ('bo_table','board_no','mid','kboard_id','page_id') if q.get(k)}
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(routing),''))


def summarize(pages):
    writes=[p for p in pages if p.get('stage')=='write'];logins=[p for p in pages if p.get('stage')=='login' or p.get('actual_page_role')=='login'];signups=[p for p in pages if p.get('stage')=='signup']
    checks=[{'stage':p['stage'],'url':p['url'],**p['human_check']} for p in pages if p.get('human_check',{}).get('scope')!='not_observed' and p.get('human_check')]
    gated=[p for p in writes if p.get('form_state')=='login_required' and p.get('write_mode')!='existing_item_related']
    forms=[p for p in writes if p.get('form_state')=='measured' and p.get('write_mode')!='existing_item_related']
    specific=[p for p in forms if p.get('purpose_hint',{}).get('kind') not in (None,'purpose_not_verified')]
    code=any(p.get('restricted_membership_hint') for p in pages)
    scopes={x['scope'] for x in checks}
    if 'page_access' in scopes:state='page_access_human_required'
    elif code or any(p.get('access_reason') for p in pages):state='access_review_required'
    elif 'write' in scopes:state='write_human_required'
    elif gated:state='account_verification_pending'
    elif specific and len(specific)==len(forms):state='purpose_review_required'
    elif forms:state='write_form_observed'
    elif any(p.get('authentication_form_state',p.get('form_state'))=='measured' for p in logins):state='login_profile_observed'
    elif signups:state='signup_route_observed'
    elif 'comment_or_embedded_unconfirmed' in scopes or 'integration_only_unconfirmed' in scopes:state='human_scope_review'
    elif not any(200<=p.get('http',0)<400 for p in pages):state='network_review'
    else:state='navigation_review_required'
    next_action={
        'account_verification_pending':'정상 가입·로그인 검증을 먼저 진행한 뒤, 인증된 상태에서 같은 게시판의 글쓰기 화면을 다시 확인해야 합니다.',
        'page_access_human_required':'페이지 접근 단계에서 사람 확인이 필요합니다. 우회하지 않고 해당 단계에서 대기합니다.',
        'write_human_required':'글쓰기 화면의 사람 확인 절차가 감지됐습니다. 자동 제출하지 않고 해당 단계에서 대기합니다.',
        'access_review_required':'전용 권한·관리자 제한을 확인해야 합니다. 제한을 해제하거나 우회하지 않습니다.',
        'purpose_review_required':'상담·후기 등 게시판 용도와 홍보 허용을 검토해야 합니다. 일반 홍보 발행 대상으로 세지 않습니다.',
        'write_form_observed':'양식 확인 단계입니다. 홍보 허용·필수 입력칸을 검토한 뒤 발행과 공개 제목·본문을 별도로 검증해야 합니다.',
        'login_profile_observed':'로그인 기능은 확인했습니다. 글쓰기에 로그인이 필수인지와 실제 인증 성공은 아직 미확인입니다.',
        'signup_route_observed':'가입 주소 또는 약관 화면을 확인했습니다. 동의·가입·인증은 수행하지 않았습니다.'}.get(state,'메뉴·동적 화면 또는 접속 상태를 더 확인해야 합니다. 미발견을 탈락으로 처리하지 않습니다.')
    if state=='human_scope_review':next_action='댓글·내장 보안 기능 또는 연동 코드 신호입니다. 글쓰기 단계에서 필요한 절차인지는 아직 확인되지 않았습니다.'
    profile={
        'signup':{'status':'form_observed' if any(p.get('form_state')=='measured' for p in signups) else 'terms_gate_observed' if any(p.get('terms_gate_observed') for p in signups) else 'route_observed' if signups else 'not_discovered',
                  'urls':[p['url'] for p in signups],'actual_signup_verified':False,'terms_accepted':False,
                  'identity_verification_hint':any(p.get('identity_verification_hint') for p in signups),'email_verification_hint':any(p.get('email_verification_hint') for p in signups)},
        'login':{'status':'form_observed' if any(p.get('authentication_form_state',p.get('form_state'))=='measured' for p in logins) else 'route_observed' if logins else 'not_discovered',
                 'urls':[p['url'] for p in logins],'actual_login_verified':False},
        'write':{'status':'authentication_required_before_recheck' if gated else 'form_observed' if forms else 'not_discovered',
                 'authenticated_recheck_required':bool(gated),'authenticated_recheck_done':False,
                 'boards':[{'url':p.get('requested_url',p['url']),'response_url':p['url'],'board_key':board_key(p.get('requested_url',p['url'])),'write_mode':p.get('write_mode'),'form_state':p.get('form_state'),'human_scope':p.get('human_check',{}).get('scope'),'purpose_hint':p.get('purpose_hint'),'required_fields':[{'name':f.get('name'),'label':f.get('label'),'role':f.get('role')} for f in p.get('fields',[]) if f.get('required')]} for p in writes]},
        'promotion_permission':{'status':'not_verified','verified':False},
        'publish':{'status':'not_attempted','verified':False},'public_title_body':{'status':'not_attempted','verified':False}}
    return {'analysis_version':ANALYSIS_VERSION,'verification_depth':'unauthenticated_navigation','state':state,'next_action':next_action,
            'flow_profile':profile,'human_checks':checks,'automatic_rejection':False,'publication_verified':False,'auto_ready':False}
