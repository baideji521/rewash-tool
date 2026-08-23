# ============================================================
# 一键上传 GitHub（自动跳过超过 1MB 的文件）
# 目标仓库: https://github.com/baideji521/rewash-tool
# 用法:
#   powershell -ExecutionPolicy Bypass -File upload_github.ps1
#   加 -DryRun 仅扫描预览，不提交不推送
# ============================================================
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Limit   = 1MB
$RepoUrl = "https://github.com/baideji521/rewash-tool.git"

Write-Host "=== 一键上传 GitHub（跳过 >1MB 文件）===" -ForegroundColor Cyan
if ($DryRun) { Write-Host "[试运行] 只扫描预览，不提交不推送" -ForegroundColor Yellow }

# ── git 可用性与仓库检查 ──
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "未检测到 git，请先安装 Git for Windows" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".git")) {
    Write-Host "当前目录不是 git 仓库，正在初始化..." -ForegroundColor Yellow
    git init | Out-Null
}
$remotes = @(git remote)
if ($remotes -notcontains "origin") {
    git remote add origin $RepoUrl
    Write-Host "已添加远程仓库: $RepoUrl"
}

# ── 1) 扫描超过 1MB 的文件（排除 .git 目录）──
$gitDir = Join-Path $PSScriptRoot ".git"
$big = @(Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { -not $_.FullName.StartsWith($gitDir) -and $_.Length -gt $Limit })

Write-Host ("超过 1MB 跳过的文件共 {0} 个:" -f $big.Count) -ForegroundColor Yellow
foreach ($f in $big) {
    Write-Host ("  - {0}  ({1:N2} MB)" -f
        $f.FullName.Substring($PSScriptRoot.Length + 1), ($f.Length / 1MB))
}

# ── 试运行到此为止 ──
if ($DryRun) {
    $n = @(git status --porcelain | Where-Object { $_ }).Count
    Write-Host ("[试运行] 当前待上传变更 {0} 项；没有修改 Git 或文件。" -f $n) -ForegroundColor Green
    exit 0
}

# ── 2) 暂存全部，再以暂存区为准拦截大文件 ──
# 不能只靠 .gitignore：它无法拦截此前已暂存或已被 Git 跟踪的文件。
git add -A
if ($LASTEXITCODE -ne 0) {
    Write-Host "暂存失败，请检查 Git 状态。" -ForegroundColor Red
    exit 1
}

# 只检查当前仍存在的暂存文件；删除大文件是缩小仓库，允许提交删除操作。
$oversizeStaged = @()
$stagedNames = @(git diff --cached --name-only)
foreach ($rp in $stagedNames) {
    if (-not $rp) { continue }
    $full = Join-Path $PSScriptRoot $rp
    if ((Test-Path -LiteralPath $full -PathType Leaf) -and
        (Get-Item -LiteralPath $full).Length -gt $Limit) {
        $oversizeStaged += $rp
    }
}
foreach ($rp in $oversizeStaged) {
    git restore --staged -- $rp
    if ($LASTEXITCODE -ne 0) {
        Write-Host "无法取消暂存大文件: $rp" -ForegroundColor Red
        exit 1
    }
    Write-Host "  已跳过超过 1MB 的文件: $rp" -ForegroundColor Yellow
}

$staged = @(git diff --cached --name-only | Where-Object { $_ })
if ($staged.Count -eq 0) {
    Write-Host "没有需要上传的变更，仓库已是最新。" -ForegroundColor Green
    exit 0
}
Write-Host ("本次上传 {0} 个文件变更" -f $staged.Count) -ForegroundColor Cyan

# ── 3) 提交 ──
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
git commit -m "auto upload $ts"
if ($LASTEXITCODE -ne 0) {
    Write-Host "提交失败（可能未配置 git user.name/user.email）" -ForegroundColor Red
    exit 1
}

# ── 4) 推送（被拒绝则 rebase 后重推）──
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
git push origin $branch
if ($LASTEXITCODE -ne 0) {
    Write-Host "推送被拒绝，尝试 pull --rebase 后重推..." -ForegroundColor Yellow
    git pull --rebase origin $branch
    git push origin $branch
    if ($LASTEXITCODE -ne 0) {
        Write-Host "推送仍失败，请检查网络或 GitHub 凭据" -ForegroundColor Red
        exit 1
    }
}
Write-Host ("✓ 上传完成: {0} (分支 {1})" -f $RepoUrl, $branch) -ForegroundColor Green
