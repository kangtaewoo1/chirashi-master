#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""데이터 2차 정제(self-training): 재학습 모델(93.7%) + ddddocr 다중전처리를 함께 투표해
버려졌던 790개에서 '강한 합의' 라벨을 더 건진다. 결과: captcha_retrain/clean2 (확대 정제셋)."""
import os, sys, re, io, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DS   = os.path.join(os.path.dirname(HERE), 'captcha_dataset')
CLEAN= os.path.join(HERE, 'clean')       # 1차 정제(그대로 포함)
OUT  = os.path.join(HERE, 'clean2')
os.makedirs(OUT, exist_ok=True)

import onnxruntime as ort
_sess = ort.InferenceSession(os.path.join(HERE,'kcaptcha_model.onnx'), providers=['CPUExecutionProvider'])
_inp = _sess.get_inputs()[0].name
import ddddocr
_dd = ddddocr.DdddOcr(show_ad=False)
DIGIT_MAP = str.maketrans({'o':'0','O':'0','l':'1','I':'1','i':'1','z':'2','Z':'2','s':'5','S':'5','b':'6','B':'8','g':'9','q':'9','D':'0','Q':'0','A':'4','t':'7','T':'7','G':'6'})
def ddnorm(r): return re.sub(r'\D','',re.sub(r'[^0-9A-Za-z]','',str(r or '')).translate(DIGIT_MAP))

def model_pred(b):
    im=ImageOps.autocontrast(Image.open(io.BytesIO(b)).convert('L')).resize((160,32),Image.BILINEAR)
    a=(np.asarray(im,dtype=np.float32)/255.0)[None,None,:,:]
    lg=_sess.run(None,{_inp:a})[0][0]; idx=lg.argmax(-1); prev=0; s=''
    for v in idx:
        v=int(v)
        if v!=prev and v!=0: s+=str(v-1)
        prev=v
    return s

def variants(b):
    outs=[b]
    try:
        im=Image.open(io.BytesIO(b)).convert('L')
        for fn in (lambda x:ImageOps.autocontrast(x.resize((x.width*2,x.height*2),Image.LANCZOS)),
                   lambda x:x.point(lambda p:0 if p<128 else 255).resize((x.width*2,x.height*2),Image.LANCZOS),
                   lambda x:ImageOps.autocontrast(x.filter(ImageFilter.MedianFilter(3)).resize((x.width*2,x.height*2),Image.LANCZOS))):
            bb=io.BytesIO(); fn(im).save(bb,'PNG'); outs.append(bb.getvalue())
    except Exception: pass
    return outs

def consensus(fname, b):
    votes=[]
    # 신모델은 2표(더 정확하므로 가중)
    m=model_pred(b)
    if len(m)==6: votes += [m, m]
    # ddddocr 다중전처리
    for v in variants(b):
        try:
            r=ddnorm(_dd.classification(v))
            if len(r)==6: votes.append(r)
        except Exception: pass
    # 파일명 라벨(6자리면 1표)
    fm=re.match(r'([0-9]+)_',fname)
    if fm and len(fm.group(1))==6: votes.append(fm.group(1))
    if not votes: return None
    lab,cnt=Counter(votes).most_common(1)[0]
    # 강한 합의: 3표 이상 + 최다표가 전체의 과반
    if cnt>=3 and cnt> len(votes)/2: return lab
    return None

def main():
    seen=set(); kept=0
    # 1차 정제 그대로 승계
    for f in os.listdir(CLEAN):
        if not f.endswith('.png'): continue
        b=open(os.path.join(CLEAN,f),'rb').read(); h=hashlib.md5(b).hexdigest()
        seen.add(h)
        with open(os.path.join(OUT,f),'wb') as fh: fh.write(b)
        kept+=1
    base=kept
    # pending 전체 재검사(1차에서 버려진 것 포함)
    pend=os.path.join(DS,'pending'); files=[f for f in os.listdir(pend) if f.endswith('.png')]
    for i,f in enumerate(files):
        b=open(os.path.join(pend,f),'rb').read(); h=hashlib.md5(b).hexdigest()
        if h in seen: continue
        lab=consensus(f,b)
        if lab:
            seen.add(h)
            hh=h[:8]
            with open(os.path.join(OUT,f'{lab}_{hh}.png'),'wb') as fh: fh.write(b)
            kept+=1
        if (i+1)%200==0: print(f'  진행 {i+1}/{len(files)} · 누적 {kept}')
    print(f'=== 2차 정제 완료: {kept}개 (1차 {base} + 추가 {kept-base}) → {OUT}')

if __name__=='__main__':
    main()
