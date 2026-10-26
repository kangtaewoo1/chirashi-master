"""Read-only, value-free form measurement for heterogeneous board templates."""
import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode


def merge_flow_knowledge(previous, definition, event=None):
    """Version an adapter's value-free recipe and retain distinct live evidence stages."""
    origin = definition['origin']
    base = urlsplit(origin)
    if base.scheme != 'https' or not base.hostname or base.username or base.password or base.path:
        raise ValueError('Invalid knowledge origin')
    recipe = {'origin': origin, 'adapter': definition['adapter'], 'fields': [], 'routes': {},
              'limits': dict(definition.get('limits') or {})}
    for field in definition['fields']:
        # Actual values, secrets, contact details and remote error text never enter this store.
        clean = {k: field[k] for k in ('name', 'role', 'required', 'type', 'selector') if k in field}
        recipe['fields'].append(clean)
    for name, url in definition['routes'].items():
        p = urlsplit(url)
        if (p.scheme != base.scheme or p.netloc != base.netloc or p.username or p.password
                or p.query or p.fragment):
            raise ValueError('Cross-origin or private knowledge URL')
        recipe['routes'][name] = url
    fingerprint = hashlib.sha256(json.dumps(recipe, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    previous = previous or {}
    changed = previous.get('fingerprint') != fingerprint
    history = list(previous.get('history') or [])
    if previous and changed:
        history.append({'version': previous.get('version'), 'fingerprint': previous.get('fingerprint'),
                        'evidence': previous.get('evidence') or {}})
    evidence = {} if changed else dict(previous.get('evidence') or {})
    if event:
        stage = event.get('stage'); state = event.get('state')
        allowed = {'signup': {'created', 'restricted', 'duplicate', 'human_required', 'unknown', 'rejected'},
                   'login': {'verified', 'review'}, 'server_post': {'verified'}, 'browser_post': {'verified'}}
        if stage not in allowed or state not in allowed[stage]: raise ValueError('Unknown evidence stage')
        item = {'state': state, 'checked_at': datetime.now(timezone.utc).isoformat()}
        if stage in ('server_post', 'browser_post'):
            receipt = event.get('receipt') or {}; p = urlsplit(str(receipt.get('url') or ''))
            if (p.scheme != base.scheme or p.netloc != base.netloc or p.username or p.password or p.query
                    or p.fragment or not receipt.get('title_match') or not receipt.get('body_match')
                    or receipt.get('public_read_without_authentication') is not True):
                raise ValueError('Public post receipt required')
            if stage == 'server_post' and receipt.get('transport') != definition['adapter']+'_server_browser':
                raise ValueError('Browser-only evidence is not server evidence')
            item.update(url=receipt['url'], title_match=True, body_match=True, public_read=True)
        evidence[stage] = item
    return {**recipe, 'fingerprint': fingerprint, 'version': int(previous.get('version', 0)) + int(changed),
            'history': history[-20:], 'evidence': evidence,
            'implementation_state': 'adapter_implemented',
            'server_publish_verified': (evidence.get('server_post') or {}).get('state') == 'verified'}

class Node:
    def __init__(self, tag='', attrs=None, parent=None):
        self.tag=tag; self.attrs=dict(attrs or []); self.parent=parent; self.children=[]; self.parts=[]
    def text(self):
        if self.tag in {'textarea','script','style'}: return ''
        return ' '.join(' '.join(self.parts+[c.text() for c in self.children]).split())

class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.root=Node(); self.current=self.root; self.nodes=[]
    def handle_starttag(self,tag,attrs):
        node=Node(tag,attrs,self.current); self.current.children.append(node); self.nodes.append(node)
        if tag not in {'input','img','br','hr','meta','link','area','base','embed','source','wbr','col','param'}: self.current=node
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs); self.handle_endtag(tag)
    def handle_endtag(self,tag):
        n=self.current
        while n.parent:
            if n.tag==tag: self.current=n.parent; return
            n=n.parent
    def handle_data(self,data):
        if self.current.tag not in {'script','style'}: self.current.parts.append(data)

def role_for(field):
    if field.get('type') in {'email','tel','file'}: return {'email':'email','tel':'phone','file':'attachment'}[field['type']]
    key=(field.get('name','')+' '+field.get('id','')+' '+field.get('autocomplete','')).lower()
    label=field.get('label','')+' '+field.get('placeholder','')
    if field.get('type')=='password':
        return 'password_confirm' if re.search(r'confirm|check|(?:password|passwd|pw)_?re\b|확인',key+' '+label) else 'password'
    for role,pattern in [
        ('captcha',r'captcha|recaptcha|turnstile|자동등록방지'),
        ('title',r'wr_subject|subject|제목'),('content',r'wr_content|content|본문|내용'),
        ('email',r'email|e_mail|이메일|전자우편'),('nickname',r'mb_nick|nickname|닉네임|별명'),
        ('id',r'mb_id|member_id|login_id|user_?id|아이디'),
        ('phone',r'mobile|phone|(?:^|[ _])tel|휴대|전화'),
        ('post_visibility',r'\b(?:secret|is_secret)\b|비밀글'),
        ('name',r'wr_name|mb_name|member_name|user_name|real_name|작성자|이름|성명'),
        ('address',r'zipcode|postcode|address|addr|우편|주소'),
        ('agreement',r'agree|consent|약관|동의')]:
        if re.search(pattern,key+' '+label,re.I): return role
    return 'unknown'

def _anc(node,tag):
    while node:
        if node.tag==tag: return node
        node=node.parent

def _hidden(node):
    while node:
        a=node.attrs
        if 'hidden' in a or a.get('aria-hidden')=='true' or set(a.get('class','').split()) & {'displaynone','d-none','hidden'} or re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)',a.get('style',''),re.I): return True
        node=node.parent
    return False

def safe_url(url):
    p=urlsplit(url)
    # Preserve board routing, omit transient authentication/challenge query tokens.
    query=urlencode([(k,v) for k,v in parse_qsl(p.query) if k in {'board_no','bo_table'}])
    host=p.hostname or ''
    if ':' in host: host='['+host+']'
    if p.port: host+=':'+str(p.port)
    return urlunsplit((p.scheme,host,p.path,query,''))

def measure_html(markup,url,kind):
    if kind not in {'signup','write'}: raise ValueError('kind must be signup or write')
    p=Parser(); p.feed(markup); p.close()
    labels={n.attrs.get('for'):n.text() for n in p.nodes if n.tag=='label' and n.attrs.get('for')}
    forms=[]
    for form in [n for n in p.nodes if n.tag=='form']:
        fields=[]
        for n in p.nodes:
            if n.tag not in {'input','select','textarea','button','iframe'}: continue
            if _anc(n,'form') is not form and not (n.attrs.get('form') and n.attrs['form']==form.attrs.get('id')): continue
            a=n.attrs; typ=a.get('type','text' if n.tag=='input' else n.tag).lower()
            if typ in {'submit','reset','button','image'} or n.tag=='button': continue
            f={k:a[k] for k in ('name','id','minlength','maxlength','pattern','autocomplete','placeholder','accept','multiple') if k in a}
            f.update(tag=n.tag,type=typ,disabled='disabled' in a,readonly='readonly' in a,hidden=(typ=='hidden' or _hidden(n)))
            label=labels.get(a.get('id'),'')
            if not label:
                wrap=_anc(n.parent,'label')
                if wrap: label=wrap.text()
            row=_anc(n,'tr')
            if not label and row:
                cell=_anc(n,'td')
                if cell in row.children:
                    preceding=row.children[:row.children.index(cell)]
                    label=next((x.text() for x in reversed(preceding) if x.tag=='td' and x.text()),'')
            if not label and row:
                label=' '.join(x.text() for x in row.children if x.tag=='th')
            f['label']=label[:240]
            native='required' in a or a.get('aria-required')=='true' or 'required' in a.get('class','').split() or a.get('data-required')=='true'
            # A label marked required is evidence; lack of a marker is not evidence of optionality.
            marked=bool(re.search(r'필수|\brequired\b',label,re.I))
            if row:
                marked=marked or any(x.tag=='img' and not _hidden(x) and re.search(r'필수|required',x.attrs.get('alt',''),re.I) for x in p.nodes if _anc(x,'tr') is row)
            f['required']=True if native or marked else (False if a.get('aria-required')=='false' else None)
            f['required_source']='attribute' if native or a.get('aria-required')=='false' else ('label' if marked else 'unknown')
            if n.tag=='select':
                f['options']=[{'value':x.attrs.get('value',''), 'label':x.text()[:120], 'disabled':'disabled' in x.attrs} for x in p.nodes if x.tag=='option' and _anc(x,'select') is n]
            f['role']=role_for(f)
            if f['role'] in {'id','password','password_confirm'}:
                context=(row or n.parent).text()
                hint=re.search(r'(\d+)\s*(?:자)?\s*[~∼-]\s*(\d+)\s*(?:자|글자)',context)
                if hint: f['text_constraints']={'min_length':int(hint.group(1)),'max_length':int(hint.group(2)),'source':'nearby_text'}
            # Use quoted attribute selectors, escaping quotes and backslashes. Never store field values.
            ident=f.get('id'); name=f.get('name'); selector=''
            if ident or name:
                val=(ident or name).replace('\\','\\\\').replace('"','\\"')
                selector='['+('id' if ident else 'name')+'="'+val+'"]'
            f['selector']=selector
            fields.append(f)
        action=safe_url(urljoin(url,form.attrs.get('action','')))
        forms.append({'id':form.attrs.get('id',''),'action':action,'method':form.attrs.get('method','get').lower(),'fields':fields})
    def score(form):
        roles=[f['role'] for f in form['fields'] if not f['hidden'] and not f['disabled']]
        action=form['action'].lower()
        if kind=='signup':
            if re.search(r'login|signin',action) and not re.search(r'join|register',action): return -1
            return 10*roles.count('password_confirm')+5*roles.count('password')+3*roles.count('id')+bool(re.search(r'join|register',action))
        return 10*roles.count('title')+10*roles.count('content')+bool(re.search(r'write|post',action))
    candidates=[f for f in forms if score(f)>0]
    selected=max(candidates,key=score) if candidates else None
    state='measured' if selected else 'form_unavailable'
    if selected and kind=='signup':
        roles=[f['role'] for f in selected['fields'] if not f['hidden']]
        if 'password' not in roles or not ('password_confirm' in roles or re.search(r'join|register',selected['action'],re.I)):
            state='form_unavailable'; selected=None
    if selected and kind=='write' and not {'title','content'}.issubset({f['role'] for f in selected['fields'] if not f['hidden'] and not f['disabled']}):
        state='form_unavailable'; selected=None
    evidence=(selected or {}).get('fields',[])
    challenge=bool(re.search(r'cf-turnstile|g-recaptcha|kcaptcha|veritas-hub|사람인지 확인|자동등록방지',markup,re.I))
    if not selected and kind=='write' and any(re.search(r'login|signin',f['action'],re.I) for f in forms): state='login_required'
    if not selected and challenge: state='human_verification_required'
    if not selected and re.search(r'약관|이용약관|동의',p.root.text()): state='agreement_required' if not challenge else state
    visible_text=re.sub(r'\s+','',p.root.text())
    if not selected and len(visible_text)<300 and any(x in visible_text for x in ('접근불가합니다','접근이차단','접근이거부','AccessDenied','Forbidden')):
        state='access_denied'
    if not selected:
        # Classify server failures without retaining raw diagnostics or database identifiers.
        if re.search(r'(?:mysqli?_connect|Connect Error)',markup,re.I) and re.search(r'Access denied|SQLSTATE|Connection refused',markup,re.I):
            state='site_database_error'
        elif re.search(r'/servicedeny\.html(?:[?#]|$)',url,re.I):
            state='hosting_service_restricted'
        elif urlsplit(url).hostname=='hosting.webtro.kr' and urlsplit(url).path=='/404.html':
            state='hosting_unavailable'
        elif re.search(r'location(?:\.href|\.replace)?[^;]{0,250}login\.php',markup,re.I) and re.search(r'로그인|회원|권한',markup):
            state='login_required'
    unresolved=[f.get('name') or f.get('id') or f['tag'] for f in evidence if not f['hidden'] and not f['disabled'] and (f['role']=='unknown' or f['required'] is None)]
    canonical={'kind':kind,'form':selected,'challenge':challenge,'state':state}
    signature=hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {**canonical,'fingerprint':signature,'measured_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'source_url':safe_url(url),'unresolved':unresolved,'auto_ready':state=='measured' and not challenge and not unresolved,
            'schema_version':2,'measurement_source':'static_html'}

def changes_between(old,new):
    before={f.get('selector') or str(i):f for i,f in enumerate((old.get('form') or {}).get('fields',[]))}
    after={f.get('selector') or str(i):f for i,f in enumerate((new.get('form') or {}).get('fields',[]))}
    return {'added':sorted(after.keys()-before.keys()),'removed':sorted(before.keys()-after.keys()),
            'changed':{key:[attr for attr in sorted(set(before[key])|set(after[key])) if before[key].get(attr)!=after[key].get(attr)] for key in sorted(before.keys()&after.keys()) if before[key]!=after[key]},
            'form_changed':any((old.get('form') or {}).get(k)!=(new.get('form') or {}).get(k) for k in ('id','action','method'))}


def human_challenge_present(markup):
    """Detect active controls/widgets; an unused legacy captcha script is not a control."""
    parser=Parser(); parser.feed(markup); parser.close()
    for node in parser.nodes:
        attrs=node.attrs
        marker=' '.join(str(attrs.get(k,'')) for k in ('name','id','class','src'))
        if 'data-sitekey' in attrs or (node.tag=='script' and re.search(r'recaptcha|hcaptcha|turnstile',marker,re.I)):
            return True
        if node.tag in {'input','textarea','img','iframe','div','span'} and re.search(r'captcha|turnstile|veritas-hub|(?:^|\s)wr_key(?:\s|$)',marker,re.I):
            if 'disabled' not in attrs:
                return True
    return bool(re.search(r'veritas-hub|사람인지\s*확인|간단한\s*확인이\s*필요',parser.root.text(),re.I))


def connection_error_state(error):
    text=str(error).lower()
    if 'certificate' in text and ('expired' in text or 'has expired' in text):
        return 'tls_certificate_expired'
    if 'certificate_verify_failed' in text or 'certificate verify failed' in text:
        return 'tls_validation_error'
    return 'connection_review'
