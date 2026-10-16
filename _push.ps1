$ErrorActionPreference = 'Stop'
Set-Location 'D:\文档\github好的项目\wan3画布'

$env:HTTPS_PROXY = 'http://127.0.0.1:10808'
$env:HTTP_PROXY  = 'http://127.0.0.1:10808'

Write-Host "=== 1. 暂存并确认清单 ==="
git add -A
git status --short | ForEach-Object { '  ' + $_ }

$staged = git diff --cached --name-only
Write-Host ("`n  共 {0} 个文件入库" -f @($staged).Count)

# 安全闸：这些路径一旦出现在暂存区就中止，绝不推上去
$forbidden = @('.env', '.env.local')
$bad = @($staged | Where-Object { $forbidden -contains $_ -or $_ -like '.env' })
if ($bad.Count -gt 0) {
  Write-Host ("`n!!! 中止：暂存区含敏感文件 {0}" -f ($bad -join ', '))
  exit 1
}
Write-Host "  安全闸通过：无 .env"

Write-Host "`n=== 2. 提交 ==="
git commit -q -m "Wan3.0 节点画布首版：原生 JS + SVG 画布，按量调阿里云百炼生成

- 后端仅用 Python 标准库：分档上传校验、wan3.0-video 异步视频、qwen-image 同步生图
- 前端零外部依赖：四类节点块、角色由内容推导、图生视频联动、运行时模式自动降级
- 启动自动清空 uploads/ 临时素材，media/ 与 cache/ 永久保留
- 五套零费用自测共 236 项断言，全程不调用任何付费生成接口"
git --no-pager log --oneline -1 | ForEach-Object { '  ' + $_ }

Write-Host "`n=== 3. 创建远程仓库并推送 ==="
gh repo create wan3-canvas --public --source=. --remote=origin --push 2>&1 | ForEach-Object { '  ' + $_ }

Write-Host "`n=== 4. 推送结果 ==="
git remote -v | ForEach-Object { '  ' + $_ }
git --no-pager log --oneline -1 | ForEach-Object { '  ' + $_ }
