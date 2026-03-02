# Auto-commit and push changes after Claude Code responses
# This hook runs on Stop (when Claude finishes responding)

$ProjectDir = if ($env:CLAUDE_PROJECT_DIR) { $env:CLAUDE_PROJECT_DIR } else { "." }
Set-Location $ProjectDir -ErrorAction SilentlyContinue

# Only proceed if this is a git repository
if (-not (Test-Path ".git")) {
    exit 0
}

# Check if there are any changes (staged or unstaged)
$diff = git diff --quiet 2>$null
$diffCached = git diff --cached --quiet 2>$null
$untracked = git ls-files --others --exclude-standard 2>$null

if ($LASTEXITCODE -eq 0 -and -not $untracked) {
    # Check staged changes too
    git diff --cached --quiet 2>$null
    if ($LASTEXITCODE -eq 0) {
        # No changes to commit
        exit 0
    }
}

# Only commit if last commit was 30+ minutes ago (batch commits)
$lastCommitTime = git log -1 --format=%ct 2>$null
if (-not $lastCommitTime) { $lastCommitTime = 0 }
$currentTime = [int][double]::Parse((Get-Date -UFormat %s))
$minutesSinceCommit = [math]::Floor(($currentTime - $lastCommitTime) / 60)

if ($minutesSinceCommit -lt 30) {
    # Too soon since last commit, skip
    exit 0
}

# Stage all changes
git add -A

# Create commit with timestamp
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$commitMessage = @"
Auto-commit from Claude Code session - $timestamp

Generated with [Claude Code](https://claude.ai/code)
via [Happy](https://happy.engineering)

Co-Authored-By: Claude <noreply@anthropic.com>
Co-Authored-By: Happy <yesreply@happy.engineering>
"@

git commit -m $commitMessage

# Push to remote
$branch = git rev-parse --abbrev-ref HEAD 2>$null
git push origin $branch 2>$null

exit 0
