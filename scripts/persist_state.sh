#!/usr/bin/env bash
set -euo pipefail

: "${STATE_PATH:=data/state.json}"
mkdir -p .state

if [[ -z "${DASHBOARD_PASSWORD:-}" ]]; then
  echo "::error::DASHBOARD_PASSWORD fehlt oder ist leer."
  exit 1
fi

python -m psa_sniper state encrypt \
  --input "$STATE_PATH" \
  --output .state/state.enc.json >/dev/null

git config user.name "psa-sniper-bot"
git config user.email "psa-sniper-bot@users.noreply.github.com"

tmp_dir="$(mktemp -d)"
cleanup() {
  git worktree remove --force "$tmp_dir" >/dev/null 2>&1 || true
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

git worktree add --detach "$tmp_dir" HEAD >/dev/null
branch_name="sniper-state-snapshot-${GITHUB_RUN_ID:-local}"
git -C "$tmp_dir" checkout --orphan "$branch_name" >/dev/null 2>&1
git -C "$tmp_dir" rm -rf . >/dev/null 2>&1 || true
cp .state/state.enc.json "$tmp_dir/state.enc.json"
cat > "$tmp_dir/README.md" <<'EOF'
# PSA Sniper encrypted state

Diese Branch enthält ausschließlich einen AES-256-GCM-verschlüsselten Scanner-State.
Nicht manuell bearbeiten. Das Klartext-Passwort liegt ausschließlich als GitHub Actions Secret vor.
EOF

git -C "$tmp_dir" add state.enc.json README.md
git -C "$tmp_dir" commit -m "Update encrypted scanner state" >/dev/null
git -C "$tmp_dir" push --force origin HEAD:sniper-state >/dev/null

echo "Verschlüsselter Scanner-State gespeichert."
