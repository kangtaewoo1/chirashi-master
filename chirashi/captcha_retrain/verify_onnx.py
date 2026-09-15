#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""학습된 kcaptcha_model.onnx를 onnxruntime으로 로드해 정확도 재검증 + 기존 ddddocr과 비교.
정제 데이터(clean) 전체에 대해 신모델 vs ddddocr 6자리 완전일치 정확도를 실측."""
import os, sys, re, io
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from PIL import Image, ImageOps
import onnxruntime as ort

HERE = os.path.dirname(os.path.abspath(__file__))
CLEAN = os.path.join(HERE, 'clean')
ONNX = os.path.join(HERE, 'kcaptcha_model.onnx')
IMG_H, IMG_W = 32, 160
BLANK = 0
def i2c(i): return str(i - 1) if 1 <= i <= 10 else ''

sess = ort.InferenceSession(ONNX, providers=['CPUExecutionProvider'])
inp = sess.get_inputs()[0].name

def prep(path):
    im = Image.open(path).convert('L')
    im = ImageOps.autocontrast(im).resize((IMG_W, IMG_H), Image.BILINEAR)
    a = np.asarray(im, dtype=np.float32) / 255.0
    return a[None, None, :, :]  # (1,1,H,W)

def onnx_pred(path):
    logits = sess.run(None, {inp: prep(path)})[0]  # (1,W,C)
    idx = logits[0].argmax(-1)
    prev = 0; s = ''
    for v in idx:
        if v != prev and v != BLANK: s += i2c(int(v))
        prev = v
    return s

def main():
    files = [f for f in os.listdir(CLEAN) if f.endswith('.png')]
    # ddddocr 비교(있으면)
    dd = None
    try:
        import ddddocr; dd = ddddocr.DdddOcr(show_ad=False)
    except Exception: pass
    DIGIT_MAP = str.maketrans({'o':'0','O':'0','l':'1','I':'1','i':'1','z':'2','Z':'2','s':'5','S':'5','b':'6','B':'8','g':'9','q':'9','D':'0','Q':'0','A':'4','t':'7','T':'7','G':'6'})
    def dd_norm(r): return re.sub(r'\D','',re.sub(r'[^0-9A-Za-z]','',str(r or '')).translate(DIGIT_MAP))
    n=ok_new=ok_dd=0
    mism=[]
    for f in files:
        lab = f.split('_')[0]
        if not (lab.isdigit() and len(lab)==6): continue
        n += 1
        p = os.path.join(CLEAN, f)
        pn = onnx_pred(p)
        if pn == lab: ok_new += 1
        elif len(mism)<8: mism.append((f, pn))
        if dd is not None:
            with open(p,'rb') as fh: b=fh.read()
            if dd_norm(dd.classification(b)) == lab: ok_dd += 1
    print(f'검증 대상 {n}개 (정제셋 전체 — 학습에 쓰였으니 상한 성능)')
    print(f'신모델(CRNN) 정확도: {ok_new/max(1,n):.1%} ({ok_new}/{n})')
    if dd is not None:
        print(f'기존 ddddocr 정확도: {ok_dd/max(1,n):.1%} ({ok_dd}/{n})')
    print('신모델 오답 샘플:', mism[:8])

if __name__ == '__main__':
    main()
