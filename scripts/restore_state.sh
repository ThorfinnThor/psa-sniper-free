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

  decrypt_error=".state/decrypt.err"
  if python -m psa_sniper state decrypt \
    --input .state/state.enc.json \
    --output "$STATE_PATH" >/dev/null 2>"$decrypt_error"; then
    rm -f "$decrypt_error"
    echo "Verschlüsselter Scanner-State wiederhergestellt."
  elif grep -q "Entschlüsselung fehlgeschlagen" "$decrypt_error"; then
    rm -f "$decrypt_error" "$STATE_PATH"
    python -m psa_sniper state init --output "$STATE_PATH" >/dev/null
    echo "::warning::Der vorhandene Scanner-State kann mit dem aktuellen DASHBOARD_PASSWORD nicht entschlüsselt werden. Ein frischer State wurde initialisiert. Der alte verschlüsselte Snapshot bleibt bis zum erfolgreichen Persist-Schritt unverändert."
    echo "State-Reset nach Passwortwechsel oder inkompatiblem verschlüsselten Snapshot."
  else
    echo "::error::Scanner-State konnte aus einem unerwarteten Grund nicht wiederhergestellt werden."
    cat "$decrypt_error" >&2
    rm -f "$decrypt_error"
    exit 1
  fi
else
  python -m psa_sniper state init --output "$STATE_PATH" >/dev/null
  echo "Erster Lauf: neuer Scanner-State initialisiert."
fi
