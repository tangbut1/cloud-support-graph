<#
.SYNOPSIS
    一键打包并部署 cloud-support-graph 到阿里云 2C2G 服务器（默认 <YOUR_SERVER_IP>）。
.NOTES
    安全约定：
    1. 默认开启 SSH 主机指纹校验（StrictHostKeyChecking=yes），不自动清除 known_hosts，防 MITM；
       仅在确认服务器重装系统后，先在云控制台核对指纹并手动更新 ~/.ssh/known_hosts。
    2. 上传 .env（-UploadEnv）与远程启动前均强制执行生产安全闸门：
       - APP_ENV=production
       - ENABLE_DEMO_LOGIN=false（禁止公网免密登录）
       - JWT_SECRET 长度 >= 32 且不含占位关键词
       - SEED_DEFAULT_PASSWORD 长度 >= 10 且不得为仓库公开口令 Passw0rd!
    3. 如服务器已备好域名与 SSL 证书（deploy/certs + deploy/nginx-tls.conf），可加 -EnableTls -PublicDomain <域名> 启用 HTTPS 443。
    4. 加 -DryRun 可在本机离线干跑并输出渲染后的远程 bash 脚本，用于语法校验（bash -n）而无需连接服务器。
.USAGE
    powershell -ExecutionPolicy Bypass -File .\deploy\deploy_aliyun.ps1
    powershell -ExecutionPolicy Bypass -File .\deploy\deploy_aliyun.ps1 -UploadEnv
    powershell -ExecutionPolicy Bypass -File .\deploy\deploy_aliyun.ps1 -EnableTls -PublicDomain support.example.com
    powershell -ExecutionPolicy Bypass -File .\deploy\deploy_aliyun.ps1 -DryRun
#>
param(
    [string]$ServerHost = "<YOUR_SERVER_IP>",
    [string]$ServerUser = "root",
    [string]$RemoteDir = "/opt/cloud-support-graph",
    [string]$PublicDomain = "",
    [switch]$UploadEnv,
    [switch]$EnableTls,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TempDir = [System.IO.Path]::GetTempPath()
$TarPath = Join-Path $TempDir "csg-deploy.tar.gz"
$SshOpts = @("-o", "StrictHostKeyChecking=yes")

function Assert-ExitZero([string]$StepName) {
    if ($LASTEXITCODE -ne 0) {
        throw "步骤失败 [$StepName]（退出码 $LASTEXITCODE）"
    }
}

function Parse-DotEnv([string]$FilePath) {
    $map = @{}
    foreach ($line in (Get-Content $FilePath -Encoding UTF8)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) { continue }
        $k = $trimmed.Substring(0, $idx).Trim()
        $v = $trimmed.Substring($idx + 1).Trim().Trim("'`"")
        $map[$k] = $v
    }
    return $map
}

function Assert-ProductionEnv([hashtable]$EnvMap, [string]$SourceLabel) {
    $appEnv = ($EnvMap["APP_ENV"] | ForEach-Object { $_.ToLower() })
    if ($appEnv -ne "production") {
        throw "拒绝部署：$SourceLabel 中 APP_ENV='$appEnv'，公网部署必须设为 APP_ENV=production"
    }
    $demo = ($EnvMap["ENABLE_DEMO_LOGIN"] | ForEach-Object { $_.ToLower() })
    if ($demo -eq "true" -or $demo -eq "1" -or $demo -eq "yes") {
        throw "拒绝部署：$SourceLabel 中 ENABLE_DEMO_LOGIN=$demo，公网严禁开启免密演示登录，请设为 false"
    }
    $jwt = [string]$EnvMap["JWT_SECRET"]
    if (-not $jwt -or $jwt.Length -lt 32 -or $jwt -match "(?i)change-this|replace-me|your-secret") {
        throw "拒绝部署：$SourceLabel 中 JWT_SECRET 长度不足 32 字节或仍为占位值，请先运行 openssl rand -hex 32 生成替换"
    }
    $seedPw = [string]$EnvMap["SEED_DEFAULT_PASSWORD"]
    if (-not $seedPw -or $seedPw.Length -lt 10 -or $seedPw -eq "Passw0rd!") {
        throw "拒绝部署：$SourceLabel 中 SEED_DEFAULT_PASSWORD 未配置、不足 10 位或仍为仓库公开口令 Passw0rd!，请配置强口令"
    }
}

# 全部远程 bash 片段均使用单引号 here-string (@'...'@)，彻底避免 PowerShell 插值破坏 $var / $(...)
$ComposeFlags = if ($EnableTls) { "-f docker-compose.yml -f deploy/docker-compose.tls.yml" } else { "-f docker-compose.yml" }
$HealthUrl = if ($EnableTls) { "https://127.0.0.1/api/health" } else { "http://127.0.0.1/api/health" }
$CurlHealthCmd = if ($EnableTls) { "curl -fsSk $HealthUrl" } else { "curl -fsS $HealthUrl" }

$TlsPreflight = if ($EnableTls) {
@'
for f in deploy/nginx-tls.conf deploy/certs/fullchain.pem deploy/certs/privkey.pem; do
  if [ ! -f "$f" ]; then
    echo "!!! 错误：启用 -EnableTls 前必须在服务器准备文件 $f（参照 deploy/nginx-tls.conf.example）"
    exit 5
  fi
done
echo '[gate] TLS 预检通过：nginx-tls.conf 与证书文件均已就绪'
'@
} else {
    "# HTTP 80 模式：无需 TLS 证书预检"
}

$RemoteScriptTemplate = @'
set -euo pipefail
trap 'rm -f /tmp/csg-remote-deploy.sh' EXIT
mkdir -p "__REMOTE_DIR__"
tar -xzf /tmp/csg-deploy.tar.gz -C "__REMOTE_DIR__"
rm -f /tmp/csg-deploy.tar.gz
cd "__REMOTE_DIR__"
bash deploy/setup_server.sh
if [ ! -f .env ]; then
  cp .env.example .env
  echo '!!! 注意：已从模板生成 .env，请编辑填入 APP_ENV=production、ENABLE_DEMO_LOGIN=false、强随机 JWT_SECRET、SEED_DEFAULT_PASSWORD、LLM_API_KEY 后再重新部署'
  exit 2
fi
chmod 600 .env
__TLS_PREFLIGHT__
python3 - <<'PY'
from pathlib import Path
env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    s = line.strip()
    if not s or s.startswith('#') or '=' not in s:
        continue
    k, v = s.split('=', 1)
    env[k.strip()] = v.strip().strip('"').strip("'")

app_env = env.get('APP_ENV', '').lower()
demo = env.get('ENABLE_DEMO_LOGIN', 'false').lower()
jwt = env.get('JWT_SECRET', '')
seed_pw = env.get('SEED_DEFAULT_PASSWORD', '')

if app_env != 'production':
    raise SystemExit(f'!!! 错误：服务器 .env 中 APP_ENV={app_env!r}，公网必须为 production')
if demo in ('true', '1', 'yes'):
    raise SystemExit('!!! 错误：服务器 .env 开启了 ENABLE_DEMO_LOGIN=true，公网严禁免密登录')
if len(jwt) < 32 or any(bad in jwt.lower() for bad in ('change-this', 'replace-me', 'your-secret')):
    raise SystemExit('!!! 错误：服务器 .env 中 JWT_SECRET 长度不足 32 或仍为占位值')
if len(seed_pw) < 10 or seed_pw == 'Passw0rd!':
    raise SystemExit('!!! 错误：服务器 .env 中 SEED_DEFAULT_PASSWORD 未设置、不足 10 位或仍为 Passw0rd!')
print('[gate] 生产安全闸门校验通过 (APP_ENV=production, ENABLE_DEMO_LOGIN=false, strong JWT/SEED_DEFAULT_PASSWORD)')
PY
docker compose __COMPOSE_FLAGS__ --profile full build
docker compose __COMPOSE_FLAGS__ --profile full up -d
echo '等待后端容器健康检查通过...'
HEALTH_OK=0
for i in $(seq 1 30); do
  if __CURL_HEALTH__ >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 2
done
if [ "$HEALTH_OK" -ne 1 ]; then
  echo '!!! 错误：健康检查超时未通过'
  docker compose __COMPOSE_FLAGS__ logs --tail=50 backend nginx
  exit 4
fi
docker compose __COMPOSE_FLAGS__ exec -T backend python -m app.db.seed --rotate-weak
# 二次安全复核：① 全部 4 个预设账号均不可用仓库公开口令 Passw0rd! 登录(401)；② 校验 SEED_DEFAULT_PASSWORD 非空并测试 admin 登录态
docker compose __COMPOSE_FLAGS__ exec -T backend python - <<'PY'
import json, os, urllib.error, urllib.request

def post_login(username: str, password: str) -> int:
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

for uname in ("zhangsan", "lisi", "wanggong", "admin"):
    st = post_login(uname, "Passw0rd!")
    if st != 401:
        raise SystemExit(f"!!! 安全告警：账号 {uname} 仍可用公开口令 Passw0rd! 登录（HTTP {st}），部署中止")

new_pw = os.environ.get("SEED_DEFAULT_PASSWORD", "").strip()
if not new_pw or new_pw == "Passw0rd!" or len(new_pw) < 10:
    raise SystemExit("!!! 安全告警：容器环境缺少合规 SEED_DEFAULT_PASSWORD，拒绝跳过复核")

ok_st = post_login("admin", new_pw)
if ok_st == 200:
    print("[gate] 口令轮换双复核通过：4 账号公开弱口令均被拒绝 (401) 且 admin 新强口令登录成功 (200)")
elif ok_st == 401:
    print("[gate] 弱口令复核通过：4 账号公开弱口令均被拒绝 (401)；注：admin 当前使用独立已轮换强口令（如需强制统一请运行 seed --rotate-all）")
else:
    raise SystemExit(f"!!! 异常响应：admin 登录检查返回 HTTP {ok_st}")
PY
__CURL_HEALTH__
echo ''
docker compose __COMPOSE_FLAGS__ ps
'@

$RemoteScript = $RemoteScriptTemplate.
    Replace('__REMOTE_DIR__', $RemoteDir).
    Replace('__COMPOSE_FLAGS__', $ComposeFlags).
    Replace('__CURL_HEALTH__', $CurlHealthCmd).
    Replace('__TLS_PREFLIGHT__', $TlsPreflight).
    Replace("`r`n", "`n")

if ($DryRun) {
    Write-Output $RemoteScript
    exit 0
}

Write-Host "[1/5] 本地构建前端静态产物 (frontend/dist)..." -ForegroundColor Cyan
Push-Location (Join-Path $RepoRoot "frontend")
try {
    npm run build
    Assert-ExitZero "npm run build"
} finally {
    Pop-Location
}

Write-Host "[2/5] 打包项目代码与 frontend/dist（排除 .venv / node_modules / data / .env / .git）..." -ForegroundColor Cyan
if (Test-Path $TarPath) { Remove-Item $TarPath -Force }
Push-Location $RepoRoot
try {
    tar `
        --exclude=".git" `
        --exclude=".env" `
        --exclude="backend/.venv" `
        --exclude="backend/__pycache__" `
        --exclude="backend/.pytest_cache" `
        --exclude="frontend/node_modules" `
        --exclude="frontend/.vite" `
        --exclude="data" `
        --exclude="docs/report-assets" `
        -czf $TarPath .
    Assert-ExitZero "tar 打包"
} finally {
    Pop-Location
}
$SizeMB = [math]::Round((Get-Item $TarPath).Length / 1MB, 2)
Write-Host "    产物已生成: $TarPath ($SizeMB MB)"

if ($UploadEnv) {
    $LocalEnv = Join-Path $RepoRoot ".env"
    if (-not (Test-Path $LocalEnv)) {
        throw "未找到本地 .env 文件: $LocalEnv"
    }
    $LocalEnvMap = Parse-DotEnv $LocalEnv
    Assert-ProductionEnv $LocalEnvMap "本地 .env"
}

Write-Host "[3/5] 使用固定主机指纹上传部署包到 ${ServerUser}@${ServerHost}..." -ForegroundColor Cyan
Write-Host "    提示：若首次连接或重装系统后指纹校验失败，请先核对控制台指纹并更新 ~/.ssh/known_hosts。" -ForegroundColor DarkGray
scp @SshOpts $TarPath "${ServerUser}@${ServerHost}:/tmp/csg-deploy.tar.gz"
Assert-ExitZero "scp 部署包"

if ($UploadEnv) {
    Write-Host "    本地生产 .env 安全闸门已通过，正在加密传输至服务器..." -ForegroundColor Yellow
    ssh @SshOpts "${ServerUser}@${ServerHost}" "mkdir -p $RemoteDir"
    Assert-ExitZero "ssh 创建远程目录"
    scp @SshOpts $LocalEnv "${ServerUser}@${ServerHost}:${RemoteDir}/.env"
    Assert-ExitZero "scp 上传 .env"
}

Write-Host "[4/5] 通过安全文件传输写入远程脚本并执行生产闸门校验与 Docker Compose 启动 ($ComposeFlags)..." -ForegroundColor Cyan
# 不通过 ssh 命令行参数传递多行脚本（Windows PowerShell 5.1 会剥离原生参数中的双引号），
# 而是先以 UTF-8 无 BOM + LF 落盘并通过 scp 原样传输后在远端 bash 执行
$RemoteScriptLocalPath = Join-Path $TempDir "csg-remote-deploy.sh"
[System.IO.File]::WriteAllText(
    $RemoteScriptLocalPath,
    $RemoteScript + "`n",
    (New-Object System.Text.UTF8Encoding $false)
)
try {
    scp @SshOpts $RemoteScriptLocalPath "${ServerUser}@${ServerHost}:/tmp/csg-remote-deploy.sh"
    Assert-ExitZero "scp 远程部署脚本"
    ssh @SshOpts "${ServerUser}@${ServerHost}" "bash /tmp/csg-remote-deploy.sh"
    Assert-ExitZero "ssh 远程部署"
} finally {
    Remove-Item $RemoteScriptLocalPath -Force -ErrorAction SilentlyContinue
}

Write-Host "[5/5] 清理本地临时压缩包..." -ForegroundColor Cyan
Remove-Item $TarPath -Force -ErrorAction SilentlyContinue
if ($EnableTls) {
    if ($PublicDomain) {
        $VerifyCmd = "..\deploy\verify_public.py https://${PublicDomain}"
    } else {
        $VerifyCmd = "..\deploy\verify_public.py https://${ServerHost} --insecure"
        Write-Host "提示：未指定 -PublicDomain，用 IP 访问自签/域名证书时验证脚本需加 --insecure；生产域名验收请传入 https://<域名> 且不加 --insecure。" -ForegroundColor Yellow
    }
} else {
    $VerifyCmd = "..\deploy\verify_public.py http://${ServerHost}"
}
Write-Host "部署完成！请运行验证脚本: `$env:VERIFY_PASSWORD='<你的强口令>'; cd backend && .\.venv\Scripts\python.exe ${VerifyCmd}" -ForegroundColor Green
