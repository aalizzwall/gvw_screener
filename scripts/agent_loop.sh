#!/usr/bin/env bash
# ponytail agent loop: pi-dev fixes ready-for-agent issues, reviews open PRs.
# Issue filing stays with the 00:00 workspace patrol; this loop only fixes + reviews.
#
#   cron: 0 8 * * * /path/to/repo/scripts/agent_loop.sh
#   env:  PI_BIN (pi-dev path), PI_MODEL (gemini-4-hydrogen if GEMINI_API_KEY
#         is set, else local splash), REPO, GVW_DIR, DRY_RUN=1 for a plan-only run
set -eu
REPO=${REPO:-aalizzwall/gvw_screener}
DIR=${GVW_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
PI=${PI_BIN:-$HOME/.local/bin/pi-dev}

[ -f "$DIR/.env" ] && export GEMINI_API_KEY=${GEMINI_API_KEY:-$(sed -n 's/^GEMINI_API_KEY="\([^"]*\)".*/\1/p' "$DIR/.env")}
MODEL=${PI_MODEL:-$( [ -n "${GEMINI_API_KEY:-}" ] && echo google/gemini-4-hydrogen || echo splash/local/qwen3.8-flash-next-q2_0 )}

exec 9>/tmp/gvw-agent-loop.lock
flock 9

cd "$DIR"
git checkout -q main && git pull -q

# 1. Fix: oldest ready-for-agent issue -> branch -> pi-dev fix -> PR
issue=$(gh issue list -R "$REPO" --label ready-for-agent --state open --limit 1 --json number | jq -r '.[0].number // empty')
if [ -n "$issue" ]; then
  if [ -z "${DRY_RUN:-}" ]; then
    git checkout -q -B "fix/issue-$issue" main
    "$PI" -p --no-session --model "$MODEL" \
      "Fix GitHub issue #$issue in this repo. Minimal diff, keep all existing comments. Run tests with .venv/bin/python -m unittest discover -s tests; open a PR only if they pass: gh pr create -R $REPO -B main -H fix/issue-$issue -t 'fix: #$issue'."
    gh label create in-progress -R "$REPO" -c ededed || true
    gh issue edit "$issue" -R "$REPO" --remove-label ready-for-agent --add-label in-progress || true
  else
    echo "would fix issue #$issue on branch fix/issue-$issue with $MODEL"
  fi
fi

# 2. Review: open PRs without a ponytail-review comment get one verdict each
for pr in $(gh pr list -R "$REPO" --state open --limit 5 --json number | jq -r '.[].number'); do
  gh pr view "$pr" -R "$REPO" --json comments --jq '.comments[].body' | grep -q 'ponytail-review' && continue
  if [ -z "${DRY_RUN:-}" ]; then
    verdict=$("$PI" -p --no-session --model "$MODEL" \
      "Use the ponytail-review skill on PR #${pr} of $REPO (read it via gh pr diff ${pr}). Output only the review verdict.")
    gh pr comment "$pr" -R "$REPO" --body "ponytail-review: $verdict"
  else
    echo "would review PR #${pr} with $MODEL"
  fi
done
