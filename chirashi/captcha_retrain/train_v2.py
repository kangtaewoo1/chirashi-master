#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kcaptcha CRNN 정확도 향상 학습 하네스 (2026-09-15 대표님 '정확도 더').
환경변수로 설정을 바꿔 여러 실험을 돌리고, 고정 hold-out으로 공정 비교한다.
  DATA=clean2  SIZE=small|big  AUG=std|strong  EPOCHS=80  SEED=42  TAG=exp1
출력: kcaptcha_<TAG>.pt / kcaptcha_<TAG>.onnx + 검증정확도 stdout.
hold-out은 SEED와 무관하게 항상 같은 분할(HOLDSEED=777)로 고정 → 실험간 공정 비교.
"""
import os, sys, re, io, random, math, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from PIL import Image, ImageOps, ImageFilter
import torch, torch.nn as nn

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.environ.get('DATA','clean2'))
SIZE = os.environ.get('SIZE','small')
AUG  = os.environ.get('AUG','strong')
EPOCHS = int(os.environ.get('EPOCHS','80'))
SEED = int(os.environ.get('SEED','42'))
TAG  = os.environ.get('TAG','exp')
IMG_H, IMG_W = 32, int(os.environ.get("WIDTH","160"))
NCLASS = 11; BLANK = 0
def c2i(c): return int(c)+1
def i2c(i): return str(i-1) if 1<=i<=10 else ''

torch.manual_seed(SEED); random.seed(SEED); np.random.seed(SEED)
torch.set_num_threads(max(2, (os.cpu_count() or 8)//2))

def load_img(path, aug=False):
    im = Image.open(path).convert('L')
    if aug:
        strong = (AUG=='strong')
        ang = random.uniform(-9,9) if strong else random.uniform(-6,6)
        im = im.rotate(ang, resample=Image.BILINEAR, fillcolor=255)
        # shear(겹침 흉내) — 중간자리 오독 완화 목적
        if strong and random.random()<0.5:
            sh = random.uniform(-0.20,0.20)
            im = im.transform(im.size, Image.AFFINE, (1,sh,0,0,1,0), fillcolor=255)
        dx,dy = (random.randint(-4,4),random.randint(-3,3)) if strong else (random.randint(-3,3),random.randint(-2,2))
        im = im.transform(im.size, Image.AFFINE, (1,0,dx,0,1,dy), fillcolor=255)
        if strong and random.random()<0.3:
            im = im.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.3,0.9)))
    im = ImageOps.autocontrast(im)
    if os.environ.get("PADRIGHT"):
        # 비율유지 리사이즈 후 오른쪽 흰여백 패딩(마지막 글자에 여유 → 끝자리 오독 완화)
        tw=int(IMG_W*0.88); im=im.resize((tw,IMG_H),Image.BILINEAR)
        canvas=Image.new("L",(IMG_W,IMG_H),255); canvas.paste(im,(0,0)); im=canvas
    else:
        im = im.resize((IMG_W,IMG_H), Image.BILINEAR)
    a = np.asarray(im, dtype=np.float32)/255.0
    if aug and random.random()<0.5:
        sig = 0.07 if AUG=='strong' else 0.05
        a = np.clip(a + np.random.normal(0,sig,a.shape).astype(np.float32),0,1)
    return a

class DS(torch.utils.data.Dataset):
    def __init__(self, items, aug=False): self.items=items; self.aug=aug
    def __len__(self): return len(self.items)
    def __getitem__(self,k):
        p,lab=self.items[k]; a=load_img(p,self.aug)
        return torch.from_numpy(a).unsqueeze(0), torch.tensor([c2i(c) for c in lab],dtype=torch.long), len(lab)

def collate(b):
    return (torch.stack([x[0] for x in b]),
            torch.cat([x[1] for x in b]),
            torch.tensor([x[2] for x in b],dtype=torch.long))

class CRNN(nn.Module):
    def __init__(self, big=False):
        super().__init__()
        def blk(i,o): return nn.Sequential(nn.Conv2d(i,o,3,1,1),nn.BatchNorm2d(o),nn.ReLU(True))
        c = 256 if big else 128
        self.cnn = nn.Sequential(
            blk(1,64), nn.MaxPool2d(2),
            blk(64,128), nn.MaxPool2d(2),
            blk(128,c), blk(c,c), nn.MaxPool2d((2,1)),
            blk(c,c), nn.MaxPool2d((2,1)),
        )
        h = 256 if big else 96
        layers = 2 if big else 1
        self.rnn = nn.LSTM(c*2, h, num_layers=layers, bidirectional=True, batch_first=True, dropout=(0.2 if layers>1 else 0))
        self.fc = nn.Linear(h*2, NCLASS)
    def forward(self,x):
        f=self.cnn(x); b,c,h,w=f.shape
        f=f.permute(0,3,1,2).reshape(b,w,c*h)
        r,_=self.rnn(f); return self.fc(r)

def decode(logits):
    idx=logits.argmax(-1).cpu().numpy(); outs=[]
    for row in idx:
        prev=0; s=''
        for v in row:
            if v!=prev and v!=BLANK: s+=i2c(v)
            prev=v
        outs.append(s)
    return outs

def main():
    files=[f for f in os.listdir(DATA) if f.endswith('.png')]
    items=[(os.path.join(DATA,f), f.split('_')[0]) for f in files if f.split('_')[0].isdigit() and len(f.split('_')[0])==6]
    # 고정 hold-out(공정비교): HOLDSEED로 항상 같은 검증셋
    hold_rng=random.Random(777); idxs=list(range(len(items))); hold_rng.shuffle(idxs)
    n_val=max(40, len(items)//8)
    val_idx=set(idxs[:n_val])
    val=[items[i] for i in range(len(items)) if i in val_idx]
    train=[items[i] for i in range(len(items)) if i not in val_idx]
    print(f'[{TAG}] DATA={os.path.basename(DATA)} SIZE={SIZE} AUG={AUG} EPOCHS={EPOCHS} SEED={SEED} | 학습 {len(train)} 검증 {len(val)}')
    dl_tr=torch.utils.data.DataLoader(DS(train,True),batch_size=32,shuffle=True,collate_fn=collate)
    dl_va=torch.utils.data.DataLoader(DS(val,False),batch_size=64,shuffle=False,collate_fn=collate)
    net=CRNN(big=(SIZE=='big'))
    opt=torch.optim.Adam(net.parameters(),lr=1e-3,weight_decay=1e-5)
    sched=torch.optim.lr_scheduler.CosineAnnealingLR(opt,T_max=EPOCHS)
    ctc=nn.CTCLoss(blank=BLANK,zero_infinity=True)
    best=0.0
    for ep in range(1,EPOCHS+1):
        net.train(); tot=0
        for xs,ys,yl in dl_tr:
            lp=net(xs).log_softmax(-1).permute(1,0,2); T=lp.size(0)
            il=torch.full((xs.size(0),),T,dtype=torch.long)
            loss=ctc(lp,ys,il,yl); opt.zero_grad(); loss.backward(); opt.step(); tot+=loss.item()
        sched.step()
        net.eval(); ok=cnt=0
        with torch.no_grad():
            for xs,ys,yl in dl_va:
                pred=decode(net(xs)); off=0; gts=[]
                for L in yl.tolist(): gts.append(''.join(i2c(int(v)) for v in ys[off:off+L])); off+=L
                for p,g in zip(pred,gts): ok+=int(p==g); cnt+=1
        acc=ok/max(1,cnt)
        if acc>=best or ep==1:
            best=max(best,acc); torch.save(net.state_dict(), os.path.join(HERE,f'kcaptcha_{TAG}.pt'))
        if ep%5==0 or ep==EPOCHS or acc==best:
            print(f'[{TAG}] ep{ep:02d} loss={tot/len(dl_tr):.3f} val={acc:.1%}'+(' *' if acc==best else ''))
    print(f'[{TAG}] BEST={best:.1%}')
    # onnx 내보내기(최고 모델)
    net.load_state_dict(torch.load(os.path.join(HERE,f'kcaptcha_{TAG}.pt'))); net.eval()
    onnx_tmp=os.path.join(HERE,f'kcaptcha_{TAG}_ext.onnx')
    torch.onnx.export(net, torch.zeros(1,1,IMG_H,IMG_W), onnx_tmp,
                      input_names=['x'], output_names=['logits'],
                      dynamic_axes={'x':{0:'batch'},'logits':{0:'batch'}}, opset_version=12)
    import onnx
    m=onnx.load(onnx_tmp,load_external_data=True)
    onnx.save_model(m, os.path.join(HERE,f'kcaptcha_{TAG}.onnx'), save_as_external_data=False)
    for junk in (onnx_tmp, onnx_tmp+'.data'):
        try: os.remove(junk)
        except Exception: pass
    print(f'[{TAG}] onnx saved: kcaptcha_{TAG}.onnx  BEST={best:.1%}')
    # 결과 JSON(워크플로 수집용)
    print('RESULT_JSON '+json.dumps({'tag':TAG,'best':best,'size':SIZE,'aug':AUG,'data':os.path.basename(DATA),'seed':SEED}))

if __name__=='__main__':
    main()
