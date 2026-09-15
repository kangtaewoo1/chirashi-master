#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kcaptcha 학습데이터 자동정제 (2026-09-15 대표님 '데이터 자동정제 후 학습').
그누보드 kcaptcha = 항상 6자리 숫자(실측 확인). pending 1420개는 OCR 추정라벨이라 오답 다수.
여러 전처리로 각각 OCR → 결과가 '모두 6자리 + 서로 일치'하는 것만 신뢰 라벨로 채택(교차검증).
verified 22개는 이미 검증됨 → 그대로 포함.
출력: captcha_retrain/clean/{label}_{hash}.png  (학습용 정제 데이터)
"""
import os, sys, re, io, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
DS   = os.path.join(os.path.dirname(HERE), 'captcha_dataset')
OUT  = os.path.join(HERE, 'clean')
os.makedirs(OUT, exist_ok=True)

import ddddocr
from PIL import Image, ImageOps, ImageFilter
_ocr = ddddocr.DdddOcr(show_ad=False)

DIGIT_MAP = str.maketrans({'o':'0','O':'0','l':'1','I':'1','i':'1','z':'2','Z':'2',
                           's':'5','S':'5','b':'6','B':'8','g':'9','q':'9','D':'0',
                           'Q':'0','A':'4','t':'7','T':'7','G':'6'})

def norm_digits(raw):
    s = re.sub(r'[^0-9A-Za-z]', '', str(raw or ''))
    s = re.sub(r'\D', '', s.translate(DIGIT_MAP))
    return s

def variants(img_bytes):
    """원본 + 여러 전처리 이미지를 bytes로 반환."""
    outs = [img_bytes]
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert('L')
        # 2x 확대 + 대비
        a = ImageOps.autocontrast(im.resize((im.width*2, im.height*2), Image.LANCZOS))
        b = io.BytesIO(); a.save(b, 'PNG'); outs.append(b.getvalue())
        # 이진화(임계)
        c = im.point(lambda x: 0 if x < 128 else 255).resize((im.width*2, im.height*2), Image.LANCZOS)
        b2 = io.BytesIO(); c.save(b2, 'PNG'); outs.append(b2.getvalue())
        # median 필터로 노이즈 제거 후 대비
        d = ImageOps.autocontrast(im.filter(ImageFilter.MedianFilter(3)).resize((im.width*2, im.height*2), Image.LANCZOS))
        b3 = io.BytesIO(); d.save(b3, 'PNG'); outs.append(b3.getvalue())
    except Exception:
        pass
    return outs

def ocr_all(img_bytes):
    res = []
    for v in variants(img_bytes):
        try:
            res.append(norm_digits(_ocr.classification(v)))
        except Exception:
            res.append('')
    return res

def consensus_label(fname, img_bytes):
    """정제 규칙: 파일명 라벨 + 다중 OCR 결과 중 '6자리로 일치하는 최빈값'을 채택.
       6자리 합의가 2표 이상이면 신뢰. (파일명 라벨도 1표로 포함하되 6자리일 때만)"""
    votes = []
    m = re.match(r'([0-9]+)_', fname)
    if m and len(m.group(1)) == 6:
        votes.append(m.group(1))
    for r in ocr_all(img_bytes):
        if len(r) == 6:
            votes.append(r)
    if not votes:
        return None
    from collections import Counter
    lab, cnt = Counter(votes).most_common(1)[0]
    return lab if cnt >= 2 else None

def main():
    total = accepted = 0
    # verified: 그대로 채택(이미 검증)
    vdir = os.path.join(DS, 'verified')
    if os.path.isdir(vdir):
        for f in os.listdir(vdir):
            if not f.lower().endswith('.png'): continue
            m = re.match(r'([0-9]+)_', f)
            if not m: continue
            lab = m.group(1)
            if len(lab) != 6: continue   # 6자리만
            src = os.path.join(vdir, f)
            with open(src,'rb') as fh: data = fh.read()
            h = hashlib.md5(data).hexdigest()[:8]
            with open(os.path.join(OUT, f'{lab}_{h}.png'),'wb') as fh: fh.write(data)
            accepted += 1
    print(f'verified 채택: {accepted}')
    v_accepted = accepted
    # pending: 교차검증 합의만
    pdir = os.path.join(DS, 'pending')
    files = [f for f in os.listdir(pdir) if f.lower().endswith('.png')]
    for i, f in enumerate(files):
        total += 1
        src = os.path.join(pdir, f)
        try:
            with open(src,'rb') as fh: data = fh.read()
        except Exception:
            continue
        lab = consensus_label(f, data)
        if lab:
            h = hashlib.md5(data).hexdigest()[:8]
            with open(os.path.join(OUT, f'{lab}_{h}.png'),'wb') as fh: fh.write(data)
            accepted += 1
        if (i+1) % 200 == 0:
            print(f'  진행 {i+1}/{len(files)} · 누적채택 {accepted}')
    print(f'pending 검사 {total} · 합의채택 {accepted - v_accepted}')
    print(f'=== 정제 완료: 총 {accepted}개 (verified {v_accepted} + pending합의 {accepted - v_accepted}) → {OUT}')

if __name__ == '__main__':
    main()
