#!/bin/bash
# 정확도 향상 실험 스위트 — 고정 hold-out(seed777)으로 공정 비교. 순차 실행(CPU 경합 방지).
cd "$(dirname "$0")/.."
PY="C:/Users/aveyd/AppData/Local/Programs/Python/Python313/python.exe"
run() { echo "===== $1 ====="; DATA="$2" SIZE="$3" AUG="$4" EPOCHS="$5" SEED="$6" TAG="$1" "$PY" -u captcha_retrain/train_v2.py 2>&1 | grep -E "RESULT_JSON|BEST=|onnx saved|\] ep(90|[0-9]0) " ; }

run small_strong   clean2 small  strong 90 42
run big_strong     clean2 big    strong 90 42
run small_std      clean2 small  std    90 42
run small_seed7    clean2 small  strong 90 7
run small_seed99   clean2 small  strong 90 99
echo "===== ALL DONE ====="
