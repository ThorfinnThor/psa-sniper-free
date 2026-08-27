#!/usr/bin/env bash
set -euo pipefail

: "${STATE_PATH:=data/state.json}"
mkdir -p .state "$(dirname "$STATE_PATH")"

if [[ -z "${DASHBOARD_PASSWORD:-}" ]]; then
  echo "::error::DASHBOARD_PASSWORD fehlt oder ist leer."
  exit 1
fi

if git ls-remote --exit-code --heads origin sniper-state >/dev/null 2>&1; then
  git fetch --quiet --depth=1 origin sniper-state
  git show FETCH_HEAD:state.enc.json > .state/state.enc.json
  python -m psa_sniper state decrypt \
    --input .state/state.enc.json \
    --output "$STATE_PATH" >/dev/null
  echo "Verschlüsselter Scanner-State wiederhergestellt."
else
  python -m psa_sniper state init --output "$STATE_PATH" >/dev/null
  echo "Erster Lauf: neuer Scanner-State initialisiert."
fi
