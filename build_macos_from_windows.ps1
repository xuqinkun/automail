[CmdletBinding()]
param(
    [ValidateSet("all", "arm64", "x86_64")]
    [string]$Architecture = "all",

    [string]$OutputDirectory = "",

    [string]$Repository = "",

    [string]$Ref = "",

    [string]$ProxyUrl = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not [string]::IsNullOrWhiteSpace($ProxyUrl)) {
    [Uri]$proxyUri = $null
    if (-not [Uri]::TryCreate($ProxyUrl, [UriKind]::Absolute, [ref]$proxyUri)) {
        throw "代理地址无效：$ProxyUrl"
    }
    if ($proxyUri.Scheme -notin @("http", "https", "socks5")) {
        throw "不支持的代理协议：$($proxyUri.Scheme)（支持 http / https / socks5）"
    }
    $env:HTTP_PROXY = $ProxyUrl
    $env:HTTPS_PROXY = $ProxyUrl
    Write-Host "GitHub CLI 使用代理：$ProxyUrl"
}

function Assert-CommandSucceeded {
    param([string]$Message)
    if ($LASTEXITCODE -ne 0) {
        throw $Message
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "未找到 Git，请先安装 Git for Windows。"
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "未找到 GitHub CLI。请先运行：winget install --id GitHub.cli，然后执行 gh auth login。"
}

Push-Location $PSScriptRoot
try {
    if (-not (Test-Path ".github/workflows/build-macos.yml")) {
        throw "找不到 .github/workflows/build-macos.yml。"
    }

    & gh auth status --hostname github.com *> $null
    Assert-CommandSucceeded "GitHub CLI 尚未登录，请先执行：gh auth login"

    $changes = @(& git status --porcelain)
    Assert-CommandSucceeded "无法读取 Git 工作区状态。"
    if ($changes.Count -gt 0) {
        throw "存在未提交修改。请先 git add、git commit 并 git push，再运行本脚本。"
    }

    if ([string]::IsNullOrWhiteSpace($Ref)) {
        $Ref = (& git branch --show-current).Trim()
        Assert-CommandSucceeded "无法读取当前 Git 分支。"
    }
    if ([string]::IsNullOrWhiteSpace($Ref)) {
        throw "当前处于 detached HEAD，请通过 -Ref 指定远程分支。"
    }

    $localHead = (& git rev-parse HEAD).Trim()
    Assert-CommandSucceeded "无法读取当前提交。"

    $remoteLine = @(& git ls-remote origin "refs/heads/$Ref") | Select-Object -First 1
    Assert-CommandSucceeded "无法读取 origin/$Ref，请检查网络和远程仓库。"
    if ([string]::IsNullOrWhiteSpace($remoteLine)) {
        throw "远程分支 origin/$Ref 不存在，请先执行 git push -u origin $Ref。"
    }
    $remoteHead = ($remoteLine -split "\s+")[0]
    if ($localHead -ne $remoteHead) {
        throw "当前提交尚未推送到 origin/$Ref，请先执行 git push。"
    }

    if ([string]::IsNullOrWhiteSpace($Repository)) {
        $Repository = ((& gh repo view --json nameWithOwner --jq ".nameWithOwner") -join "").Trim()
        Assert-CommandSucceeded "无法从当前仓库识别 GitHub 项目。"
    }

    $dispatchStarted = (Get-Date).ToUniversalTime().AddSeconds(-5)
    Write-Host "触发 GitHub macOS 构建：$Repository@$Ref ($Architecture)"
    $dispatchOutput = @(
        & gh workflow run build-macos.yml `
            --repo $Repository `
            --ref $Ref `
            -f "architecture=$Architecture" 2>&1
    )
    $dispatchExitCode = $LASTEXITCODE
    $dispatchOutput | ForEach-Object { Write-Host $_ }

    if ($dispatchExitCode -ne 0) {
        $dispatchText = $dispatchOutput -join "`n"
        if (
            $dispatchText -match "HTTP 403" -and
            $dispatchText -match "Resource not accessible by personal access token"
        ) {
            throw @"
当前 GitHub 凭据没有触发 Actions 的权限。

如果使用 github_pat_ 开头的细粒度 PAT，请打开：
https://github.com/settings/personal-access-tokens

编辑当前 Token，并设置：
  Repository access: 包含 $Repository
  Repository permissions > Actions: Read and write

保存后重新运行本脚本。也可以改用浏览器 OAuth 登录：
  gh auth logout --hostname github.com
  gh auth login --hostname github.com --git-protocol https --web --scopes repo,workflow
"@
        }
        throw "触发 GitHub Actions 失败。"
    }

    $run = $null
    for ($attempt = 1; $attempt -le 30 -and $null -eq $run; $attempt++) {
        Start-Sleep -Seconds 2
        $runJson = & gh run list `
            --repo $Repository `
            --workflow build-macos.yml `
            --branch $Ref `
            --event workflow_dispatch `
            --commit $localHead `
            --limit 10 `
            --json databaseId,createdAt,status,url
        Assert-CommandSucceeded "查询 GitHub Actions 运行状态失败。"

        $runs = @($runJson | ConvertFrom-Json)
        $run = $runs |
            Where-Object { [DateTimeOffset]::Parse($_.createdAt) -ge $dispatchStarted } |
            Sort-Object { [DateTimeOffset]::Parse($_.createdAt) } -Descending |
            Select-Object -First 1
    }

    if ($null -eq $run) {
        throw "已触发构建，但 60 秒内未找到对应任务。请到 GitHub Actions 页面查看。"
    }

    $runId = [string]$run.databaseId
    Write-Host "构建任务：$($run.url)"
    & gh run watch $runId --repo $Repository --exit-status
    Assert-CommandSucceeded "macOS 构建失败，请打开上面的任务链接查看日志。"

    if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
        $OutputDirectory = Join-Path $PSScriptRoot "dist/github-actions"
    } elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
        $OutputDirectory = Join-Path $PSScriptRoot $OutputDirectory
    }
    $downloadDirectory = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) "run-$runId"
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null

    $artifactPattern = if ($Architecture -eq "all") {
        "AutoMail-macOS-*"
    } else {
        "AutoMail-macOS-$Architecture"
    }

    & gh run download $runId `
        --repo $Repository `
        --pattern $artifactPattern `
        --dir $downloadDirectory
    Assert-CommandSucceeded "构建成功，但下载产物失败。"

    Write-Host "`n下载完成：$downloadDirectory"
    Get-ChildItem -Path $downloadDirectory -Recurse -Filter "*.zip" |
        ForEach-Object { Write-Host " - $($_.FullName)" }
}
finally {
    Pop-Location
}
