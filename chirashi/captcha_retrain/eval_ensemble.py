#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""앙상블 + TTA 평가: 여러 onnx 모델 × 여러 전처리(TTA)의 예측을 투표해 최종 정확도 측정.
고정 hold-out(seed777, train_v2와 동일 분할)으로 공정 비교. 개별 모델 vs 앙상블 정확도 비교."""
import os, sys, io, random, glob
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from PIL import Image, ImageOps, ImageFilter
import onnxruntime as ort
from collections import Counter

HERE=os.path.dirname(os.path.abspath(__file__))
DATA=os.path.join(HERE,'clean2')
IMG_H,IMG_W=32,160
def i2c(i): return str(i-1) if 1<=i<=10 else ''

# hold-out 재현(train_v2와 동일: seed777, 1/8)
files=[f for f in os.listdir(DATA) if f.endswith('.png')]
items=[(os.path.join(DATA,f),f.split('_')[0]) for f in files if f.split('_')[0].isdigit() and len(f.split('_')[0])==6]
rng=random.Random(777); idxs=list(range(len(items))); rng.shuffle(idxs)
n_val=max(40,len(items)//8); val=[items[i] for i in idxs[:n_val]]
print('hold-out %d개'%len(val))

MODELS=sys.argv[1:] or sorted(glob.glob(os.path.join(HERE,'kcaptcha_small_*.onnx')))
sess={m:ort.InferenceSession(m,providers=['CPUExecutionProvider']) for m in MODELS}
print('모델:',[os.path.basename(m) for m in MODELS])

def tta_variants(im):
    outs=[im]
    outs.append(im.filter(ImageFilter.MedianFilter(3)))
    outs.append(ImageOps.autocontrast(im, cutoff=2))
    return outs

def infer(s, im):
    a=(np.asarray(ImageOps.autocontrast(im).resize((IMG_W,IMG_H),Image.BILINEAR),dtype=np.float32)/255.0)[None,None,:,:]
    lg=s.run(None,{s.get_inputs()[0].name:a})[0][0]; idx=lg.argmax(-1); prev=0; out=''
    for v in idx:
        v=int(v)
        if v!=prev and v!=0: out+=str(v-1)
        prev=v
    return out

def predict(path, use_tta=True, use_ens=True):
    base=Image.open(path).convert('L')
    ims=tta_variants(base) if use_tta else [base]
    models=list(sess.values()) if use_ens else [list(sess.values())[0]]
    votes=[]
    for s in models:
        for im in ims:
            r=infer(s,im)
            if len(r)==6: votes.append(r)
    if not votes: return ''
    return Counter(votes).most_common(1)[0][0]

def acc(use_tta,use_ens):
    ok=0
    for p,lab in val:
        if predict(p,use_tta,use_ens)==lab: ok+=1
    return ok/len(val), ok

# 개별 모델(각 단독, TTA 없음)
print('\n--- 개별 모델(단독, TTA off) ---')
for m in MODELS:
    only={m:sess[m]}
    ok=0
    for p,lab in val:
        r=infer(sess[m],Image.open(p).convert('L'))
        if r==lab: ok+=1
    print('  %-28s %.1f%% (%d/%d)'%(os.path.basename(m),100*ok/len(val),ok,len(val)))

a1,o1=acc(False,False); print('\n단일모델 + TTA off : %.1f%% (%d/%d)'%(100*a1,o1,len(val)))
a2,o2=acc(True,False);  print('단일모델 + TTA on  : %.1f%% (%d/%d)'%(100*a2,o2,len(val)))
a3,o3=acc(False,True);  print('앙상블  + TTA off  : %.1f%% (%d/%d)'%(100*a3,o3,len(val)))
a4,o4=acc(True,True);   print('앙상블  + TTA on   : %.1f%% (%d/%d)  ★최종'%(100*a4,o4,len(val)))
