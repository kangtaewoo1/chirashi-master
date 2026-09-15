#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kcaptcha 전용 CRNN 재학습 (2026-09-15 대표님 '캡차 모델 재학습').
정제된 6자리 숫자 캡차(captcha_retrain/clean)로 CNN+BiLSTM+CTC 모델을 학습한다.
그누보드 kcaptcha는 6자리 숫자 고정 도메인이라 좁게 특화하면 범용 ddddocr보다 정확.
학습 후 ONNX로 내보내 추론에 사용(kcaptcha_model_s.onnx). torch(CPU)만 사용.
"""
import os, sys, re, io, random, math
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from PIL import Image, ImageOps
import torch, torch.nn as nn

HERE = os.path.dirname(os.path.abspath(__file__))
CLEAN = os.path.join(HERE, 'clean')
IMG_H, IMG_W = 32, 160          # 고정 입력 크기
NCLASS = 11                     # 0-9 + blank(0)
BLANK = 0                       # CTC blank index
# 문자 → 인덱스: '0'->1 ... '9'->10, blank=0
def c2i(c): return int(c) + 1
def i2c(i): return str(i - 1) if 1 <= i <= 10 else ''

torch.manual_seed(42); random.seed(42); np.random.seed(42)

def load_img(path, aug=False):
    im = Image.open(path).convert('L')
    if aug:
        # 데이터 증강: 미세 회전 + 이동 + 노이즈 (좁은 도메인이라 과하지 않게)
        ang = random.uniform(-6, 6)
        im = im.rotate(ang, resample=Image.BILINEAR, fillcolor=255)
        dx, dy = random.randint(-3, 3), random.randint(-2, 2)
        im = im.transform(im.size, Image.AFFINE, (1, 0, dx, 0, 1, dy), fillcolor=255)
    im = ImageOps.autocontrast(im).resize((IMG_W, IMG_H), Image.BILINEAR)
    a = np.asarray(im, dtype=np.float32) / 255.0
    if aug and random.random() < 0.5:
        a = np.clip(a + np.random.normal(0, 0.05, a.shape).astype(np.float32), 0, 1)
    return a  # (H,W)

class DS(torch.utils.data.Dataset):
    def __init__(self, items, aug=False):
        self.items = items; self.aug = aug
    def __len__(self): return len(self.items)
    def __getitem__(self, k):
        path, lab = self.items[k]
        a = load_img(path, self.aug)
        x = torch.from_numpy(a).unsqueeze(0)  # (1,H,W)
        y = torch.tensor([c2i(c) for c in lab], dtype=torch.long)
        return x, y, len(lab)

def collate(batch):
    xs = torch.stack([b[0] for b in batch])
    ys = torch.cat([b[1] for b in batch])
    yl = torch.tensor([b[2] for b in batch], dtype=torch.long)
    return xs, ys, yl

class CRNN(nn.Module):
    def __init__(self):
        super().__init__()
        def blk(i, o): return nn.Sequential(nn.Conv2d(i, o, 3, 1, 1), nn.BatchNorm2d(o), nn.ReLU(True))
        self.cnn = nn.Sequential(
            blk(1, 64), nn.MaxPool2d(2),        # 16x80
            blk(64, 128), nn.MaxPool2d(2),      # 8x40
            blk(128, 128), blk(128, 128), nn.MaxPool2d((2, 1)),  # 4x40
            blk(128, 128), nn.MaxPool2d((2, 1)),                 # 2x40
        )
        self.rnn = nn.LSTM(128 * 2, 96, num_layers=1, bidirectional=True, batch_first=True)
        self.fc = nn.Linear(192, NCLASS)
    def forward(self, x):
        f = self.cnn(x)                      # (B,C,H',W')
        b, c, h, w = f.shape
        f = f.permute(0, 3, 1, 2).reshape(b, w, c * h)  # (B,W,C*H) 시퀀스
        r, _ = self.rnn(f)
        return self.fc(r)                    # (B,W,NCLASS) logits

def decode(logits):
    """CTC greedy decode → 문자열 리스트."""
    idx = logits.argmax(-1).cpu().numpy()   # (B,W)
    outs = []
    for row in idx:
        prev = 0; s = ''
        for v in row:
            if v != prev and v != BLANK: s += i2c(v)
            prev = v
        outs.append(s)
    return outs

def main():
    files = [f for f in os.listdir(CLEAN) if f.endswith('.png')]
    items = []
    for f in files:
        lab = f.split('_')[0]
        if lab.isdigit() and len(lab) == 6:
            items.append((os.path.join(CLEAN, f), lab))
    random.shuffle(items)
    n_val = max(20, len(items) // 10)
    val, train = items[:n_val], items[n_val:]
    print(f'학습 {len(train)} · 검증 {len(val)} (총 {len(items)})')

    dl_tr = torch.utils.data.DataLoader(DS(train, aug=True), batch_size=32, shuffle=True, collate_fn=collate, num_workers=0)
    dl_va = torch.utils.data.DataLoader(DS(val, aug=False), batch_size=64, shuffle=False, collate_fn=collate, num_workers=0)

    dev = 'cpu'
    net = CRNN().to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3)
    sched = torch.optim.lr_scheduler.StepLR(opt, step_size=15, gamma=0.5)
    ctc = nn.CTCLoss(blank=BLANK, zero_infinity=True)

    EPOCHS = int(os.environ.get('EPOCHS', 40))
    best = 0.0
    for ep in range(1, EPOCHS + 1):
        net.train(); tot = 0
        for xs, ys, yl in dl_tr:
            logits = net(xs)                 # (B,W,C)
            logp = logits.log_softmax(-1).permute(1, 0, 2)  # (W,B,C)
            T = logp.size(0)
            il = torch.full((xs.size(0),), T, dtype=torch.long)
            loss = ctc(logp, ys, il, yl)
            opt.zero_grad(); loss.backward(); opt.step()
            tot += loss.item()
        sched.step()
        # 검증(완전일치 정확도)
        net.eval(); ok = 0; nn_ = 0
        with torch.no_grad():
            for xs, ys, yl in dl_va:
                pred = decode(net(xs))
                # 정답 복원
                off = 0; gts = []
                for L in yl.tolist():
                    gts.append(''.join(i2c(int(v)) for v in ys[off:off+L])); off += L
                for p, g in zip(pred, gts):
                    ok += int(p == g); nn_ += 1
        acc = ok / max(1, nn_)
        print(f'ep{ep:02d} loss={tot/len(dl_tr):.3f} val_acc={acc:.1%}' + ('  *best' if acc > best else ''))
        if acc >= best:
            best = acc
            torch.save(net.state_dict(), os.path.join(HERE, 'kcaptcha_crnn_s.pt'))
    print(f'=== 최고 검증 정확도: {best:.1%} → kcaptcha_crnn_s.pt 저장 ===')

    # ONNX 내보내기(최고 모델 로드)
    net.load_state_dict(torch.load(os.path.join(HERE, 'kcaptcha_crnn_s.pt')))
    net.eval()
    dummy = torch.zeros(1, 1, IMG_H, IMG_W)
    onnx_path = os.path.join(HERE, 'kcaptcha_model_s.onnx')
    torch.onnx.export(net, dummy, onnx_path, input_names=['x'], output_names=['logits'],
                      dynamic_axes={'x': {0: 'batch'}, 'logits': {0: 'batch'}}, opset_version=12)
    print(f'ONNX 내보내기 완료 → {onnx_path}')

if __name__ == '__main__':
    main()
