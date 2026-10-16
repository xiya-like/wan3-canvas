$ErrorActionPreference = 'Continue'
Set-Location 'D:\文档\github好的项目\wan3画布'

$env:HTTPS_PROXY = 'http://127.0.0.1:10808'
$env:HTTP_PROXY  = 'http://127.0.0.1:10808'

Write-Host "=== 代理连通性 ==="
try {
  $r = Invoke-WebRequest -Uri 'https://api.github.com' -UseBasicParsing -TimeoutSec 20
  Write-Host ("  api.github.com -> HTTP {0}" -f $r.StatusCode)
} catch {
  Write-Host ("  api.github.com -> 失败: {0}" -f $_.Exception.Message)
}

Write-Host "`n=== gh 登录状态（带代理重测） ==="
gh auth status 2>&1 | ForEach-Object { '  ' + $_ }

Write-Host "`n=== 当前账号 ==="
gh api user --jq '.login' 2>&1 | ForEach-Object { '  ' + $_ }
