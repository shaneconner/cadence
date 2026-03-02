#!/bin/bash

# Auto-commit and push changes after Claude Code responses
# This hook runs on Stop (when Claude finishes responding)

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
cd "$PROJECT_DIR" || exit 0

# Only proceed if this is a git repository
if [ ! -d ".git" ]; then
  exit 0
fi

# Check if there are any changes (staged or unstaged)
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  # No changes to commit
  exit 0
fi

# Only commit if last commit was 30+ minutes ago (batch commits)
LAST_COMMIT_TIME=$(git log -1 --format=%ct 2>/dev/null || echo 0)
CURRENT_TIME=$(date +%s)
MINUTES_SINCE_COMMIT=$(( (CURRENT_TIME - LAST_COMMIT_TIME) / 60 ))

if [ "$MINUTES_SINCE_COMMIT" -lt 30 ]; then
  # Too soon since last commit, skip
  exit 0
fi

# Stage all changes
git add -A

# Create commit with timestamp
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
git commit -m "Auto-commit from Claude Code session - $TIMESTAMP

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"

# Push to remote
git push origin "$(git rev-parse --abbrev-ref HEAD)" 2>/dev/null || true

exit 0
