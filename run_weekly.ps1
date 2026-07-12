[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = $PSScriptRoot
$LogPath = Join-Path $RepoRoot "build.log"
$GeneratedFiles = @(
    "bills.json",
    "data_collected.js",
    "summaries.json",
    "gate_cache.json",
    "meeting_dates.json"
)
$BotName = "bill-tracker-bot"
$BotEmail = "bot@users.noreply.github.com"

function Protect-LogText {
    param([AllowEmptyString()][string]$Text)

    $safe = $Text -replace '(?i)(https?://)[^/@\s]+@', '$1***@'
    foreach ($name in @("ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")) {
        $secret = [Environment]::GetEnvironmentVariable($name)
        if ($secret) {
            $safe = $safe.Replace($secret, "***")
        }
    }
    return $safe
}

function Write-Log {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), (Protect-LogText $Message)
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Invoke-NativeRaw {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 turns native stderr into ErrorRecord objects.
        # Treat only the native exit code as success/failure and restore the
        # caller's preference immediately after execution.
        $ErrorActionPreference = "Continue"
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [PSCustomObject]@{
        Output = $output
        ExitCode = $exitCode
    }
}

function Invoke-LoggedNative {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )

    Write-Log "START: $Description"
    $result = Invoke-NativeRaw -Command $Command -Arguments $Arguments
    $result.Output | ForEach-Object {
        $text = Protect-LogText ($_.ToString())
        Write-Host $text
        Add-Content -LiteralPath $LogPath -Value $text -Encoding UTF8
    }
    if ($result.ExitCode -ne 0) {
        throw "$Description failed with exit code $($result.ExitCode)."
    }
    Write-Log "DONE: $Description"
}

function Get-GitOutput {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $result = Invoke-NativeRaw -Command "git" -Arguments $Arguments
    if ($result.ExitCode -ne 0) {
        $detail = ($result.Output | ForEach-Object { $_.ToString() }) -join "`n"
        throw "git $($Arguments -join ' ') failed: $detail"
    }
    return (($result.Output | ForEach-Object { $_.ToString() } |
        Where-Object { $_ -notmatch '^warning:' }) -join "`n").Trim()
}

try {
    Set-Location -LiteralPath $RepoRoot
    [System.IO.File]::WriteAllText($LogPath, "", [System.Text.UTF8Encoding]::new($false))
    Write-Log "Starting weekly high-quality update."

    # Avoid depending on a possibly unavailable per-user global ignore file.
    $worktreeResult = Invoke-NativeRaw -Command "git" -Arguments @(
        "-c", "core.excludesFile=.git/info/exclude",
        "status", "--porcelain", "--untracked-files=all"
    )
    if ($worktreeResult.ExitCode -ne 0) {
        throw "Unable to inspect the worktree."
    }
    $worktree = (($worktreeResult.Output | ForEach-Object { $_.ToString() } |
        Where-Object { $_ -notmatch '^warning:' }) -join "`n").Trim()
    if ($worktree) {
        throw "The worktree is not clean. Aborting to protect uncommitted changes."
    }

    $branch = Get-GitOutput @("branch", "--show-current")
    if (-not $branch) {
        throw "The weekly publisher cannot run from a detached HEAD."
    }

    Invoke-LoggedNative "git fetch" "git" @("fetch", "origin", $branch)
    Invoke-LoggedNative "git pull --ff-only" "git" @("pull", "--ff-only", "origin", $branch)
    $baseRemote = Get-GitOutput @("rev-parse", "refs/remotes/origin/$branch")

    $ollamaUrl = if ($env:OLLAMA_URL) { $env:OLLAMA_URL.TrimEnd("/") } else { "http://localhost:11434" }
    $ollamaUri = [Uri]$ollamaUrl
    if ($ollamaUri.Scheme -ne "http" -or $ollamaUri.Host -notin @("localhost", "127.0.0.1", "::1")) {
        throw "OLLAMA_URL must be a local HTTP endpoint."
    }
    $ollamaModel = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { "gemma4:12b" }
    Write-Log "Checking the local Ollama API and model '$ollamaModel'."
    try {
        $tags = Invoke-RestMethod -Method Get -Uri "$ollamaUrl/api/tags" -TimeoutSec 10
    }
    catch {
        throw "The local Ollama API is unavailable. Start Ollama and try again."
    }
    $availableModels = @($tags.models | ForEach-Object { if ($_.name) { $_.name } else { $_.model } })
    if ($ollamaModel -notin $availableModels) {
        throw "Ollama model '$ollamaModel' is unavailable. Pull it before running this task."
    }

    Invoke-LoggedNative "weekly high-quality pipeline" "cmd.exe" @("/d", "/c", (Join-Path $RepoRoot "run_all.bat"))
    Invoke-LoggedNative "pytest" "python" @("-m", "pytest", "-q")

    Invoke-LoggedNative "pre-publish git fetch" "git" @("fetch", "origin", $branch)
    $currentRemote = Get-GitOutput @("rev-parse", "refs/remotes/origin/$branch")
    if ($currentRemote -ne $baseRemote) {
        throw "origin/$branch changed after the run started. Aborting without an automatic merge."
    }

    Invoke-LoggedNative "stage generated files" "git" (@("add", "--") + $GeneratedFiles)
    $diffResult = Invoke-NativeRaw -Command "git" -Arguments @("diff", "--cached", "--quiet")
    $diffExitCode = $diffResult.ExitCode
    if ($diffExitCode -eq 0) {
        Write-Log "No generated files changed. Exiting successfully."
        exit 0
    }
    if ($diffExitCode -ne 1) {
        throw "Unable to inspect the staged diff (exit code $diffExitCode)."
    }

    $commitMessage = "chore: weekly high-quality bill update ({0})" -f (Get-Date -Format "yyyy-MM-dd")
    Invoke-LoggedNative "commit weekly update" "git" @(
        "-c", "user.name=$BotName",
        "-c", "user.email=$BotEmail",
        "commit", "-m", $commitMessage
    )
    Invoke-LoggedNative "push weekly update" "git" @("push", "origin", "HEAD:$branch")
    Write-Log "Published the weekly high-quality update."
    exit 0
}
catch {
    try {
        Write-Log "FAILED: $($_.Exception.Message)"
    }
    catch {
        Write-Error (Protect-LogText ($_.Exception.Message)) -ErrorAction Continue
    }
    exit 1
}
