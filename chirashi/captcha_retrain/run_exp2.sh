#!/bin/bash
cd "$(dirname "$0")/.."
PY="C:/Users/aveyd/AppData/Local/Programs/Python/Python313/python.exe"
echo "===== wide192 ====="; DATA=clean2 SIZE=small AUG=strong EPOCHS=90 SEED=42 WIDTH=192 TAG=wide192 "$PY" -u captcha_retrain/train_v2.py 2>&1 | grep -E "RESULT_JSON|BEST=|onnx saved"
echo "===== padright ====="; DATA=clean2 SIZE=small AUG=strong EPOCHS=90 SEED=42 WIDTH=192 PADRIGHT=1 TAG=padright "$PY" -u captcha_retrain/train_v2.py 2>&1 | grep -E "RESULT_JSON|BEST=|onnx saved"
echo "===== DONE ====="
