#!/usr/bin/env bash
# start-b60.sh - second llama.cpp router, on the Intel Arc Pro B60 24 GB (port 9704)
#
# Same idea as start-router.sh, but with llama.cpp's SYCL build (Intel oneAPI, Level Zero) instead of ROCm:
# ROCm only drives AMD cards. Presets in router-b60.ini, one model loaded at a time.
# The model names match router.ini on 9700, so a client picks the card by port.
# Used by the locai-b60 systemd service, but you can run it by hand too:  ./start-b60.sh
# Defaults (override with environment variables, or in ~/locai/llama/b60.env for the service):
#   B60_HOST=0.0.0.0
#   B60_PORT=9704
#   B60_DEVICE=level_zero:0     ONEAPI_DEVICE_SELECTOR value (0 = the first Level Zero GPU)
#   B60_MODELS_MAX=1
#   B60_PRESET=~/locai/models/router-b60.ini
#   B60_KEY_FILE=~/locai/llama/api-key.txt
#   LLAMA_SYCL_BIN=~/locai/llama/sycl   folder with the SYCL llama-server (b12613 was used here)
#   UMF_LIB=/opt/intel/oneapi/umf/latest/lib
set -euo pipefail

LLAMA_SYCL_BIN="${LLAMA_SYCL_BIN:-$HOME/locai/llama/sycl}"
PRESET="${B60_PRESET:-$HOME/locai/models/router-b60.ini}"
KEY_FILE="${B60_KEY_FILE:-$HOME/locai/llama/api-key.txt}"
HOST="${B60_HOST:-0.0.0.0}"
PORT="${B60_PORT:-9704}"
DEVICE="${B60_DEVICE:-level_zero:0}"
MAX="${B60_MODELS_MAX:-1}"
UMF_LIB="${UMF_LIB:-/opt/intel/oneapi/umf/latest/lib}"

[ -x "$LLAMA_SYCL_BIN/llama-server" ] || { echo "llama-server not found in $LLAMA_SYCL_BIN - unpack a llama.cpp SYCL build there (see recipes/README.md)" >&2; exit 1; }
[ -f "$PRESET" ] || { echo "preset file not found: $PRESET" >&2; exit 1; }
[ -s "$KEY_FILE" ] || { echo "API key file missing or empty: $KEY_FILE" >&2; exit 1; }
# without libumf.so.1 the SYCL build silently falls back to the slower OpenCL path
[ -f "$UMF_LIB/libumf.so.1" ] || { echo "libumf.so.1 missing in $UMF_LIB (install intel-oneapi-umf-1.2)" >&2; exit 1; }

export LD_LIBRARY_PATH="$UMF_LIB:${LD_LIBRARY_PATH:-}"
export ONEAPI_DEVICE_SELECTOR="$DEVICE"
echo "b60 router: host $HOST, port $PORT, device $DEVICE, max loaded models $MAX, presets $PRESET, llama.cpp $LLAMA_SYCL_BIN" >&2
exec "$LLAMA_SYCL_BIN/llama-server" \
  --models-preset "$PRESET" --models-max "$MAX" --jinja \
  --host "$HOST" --port "$PORT" --api-key "$(cat "$KEY_FILE")"
