"""Lossless form encoding checks; no network or submission side effects."""
import codecs
import re
from urllib.parse import urlencode, parse_qs

def form_encoding(response):
    header=response.headers.get('Content-Type','')
    meta=response.content[:8192].decode('ascii','ignore')
    match=re.search(r'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)',header,re.I) or re.search(r'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)',meta,re.I)
    if not match: raise ValueError('charset declaration missing')
    encoding=match.group(1).lower()
    if encoding in ('euc-kr','euc_kr','ks_c_5601-1987'):encoding='cp949'
    codecs.lookup(encoding)
    return encoding

def checked_payload(data,title,body,encoding):
    if data.get('wr_subject')!=title or data.get('wr_content')!=body:
        raise ValueError('title or body changed before encoding')
    if '\ufffd' in title+body:raise ValueError('replacement character in source')
    payload=urlencode(data,encoding=encoding,errors='strict').encode('ascii')
    decoded=parse_qs(payload.decode('ascii'),encoding=encoding,errors='strict',keep_blank_values=True)
    if decoded.get('wr_subject')!=[title] or decoded.get('wr_content')!=[body]:
        raise ValueError('title or body encoding roundtrip mismatch')
    return payload


def prepare_generated_article(title,body,encoding):
    """Keep visible text unchanged while making HTML transport representable."""
    import html
    title.encode(encoding,errors='strict')
    if '\ufffd' in title+body:raise ValueError('replacement character in generated source')
    prepared=body.encode(encoding,errors='xmlcharrefreplace').decode(encoding)
    if html.unescape(prepared)!=html.unescape(body):
        raise ValueError('generated article visible content changed')
    checked_payload({'wr_subject':title,'wr_content':prepared},title,prepared,encoding)
    return title,prepared
