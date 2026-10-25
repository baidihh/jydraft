#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRAMEWORKS="/Applications/VideoFusion-macOS.app/Contents/Frameworks"
mkdir -p "$ROOT/bin"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "skip: not macOS"
  exit 0
fi

if /usr/bin/clang++ -std=c++17 -O2 -arch arm64 -arch x86_64 \
  "$ROOT/jydraft/codec_dlopen.cpp" \
  "-Wl,-rpath,$FRAMEWORKS" -o "$ROOT/bin/jycodec"; then
  echo "built universal jycodec"
else
  /usr/bin/clang++ -std=c++17 -O2 "$ROOT/jydraft/codec_dlopen.cpp" \
    "-Wl,-rpath,$FRAMEWORKS" -o "$ROOT/bin/jycodec"
  echo "built native jycodec"
fi
chmod +x "$ROOT/bin/jycodec"
