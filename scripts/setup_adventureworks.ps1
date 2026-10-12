param(
    [string]$Psql = "psql",
    [string]$AdminUrl = "postgresql://postgres@localhost:5432/postgres",
    [Parameter(Mandatory = $true)][string]$AdminPassword,
    [int]$Port = 5432,
    [string]$Database = "adventureworks",
    [string]$AppUser = "adventureworks_app",
    [Parameter(Mandatory = $true)][string]$AppPassword
)

$ErrorActionPreference = "Stop"
$env:PGPASSWORD = $AdminPassword
if ($Database -notmatch '^[A-Za-z_][A-Za-z0-9_]*$' -or $AppUser -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
    throw "Database 和 AppUser 只能包含字母、数字和下划线，并且不能以数字开头。"
}
$root = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$sqlRoot = Join-Path $root "data\adventureworks"
$files = @("install-a.sql", "install-b.sql", "install-c.sql", "install-d.sql")

function Invoke-Psql([string]$Url, [string]$Sql) {
    & $Psql -X -v ON_ERROR_STOP=1 -q -d $Url -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql 执行失败：$Sql" }
}

$dbFound = (& $Psql -X -q -t -A -d $AdminUrl -c "SELECT 1 FROM pg_database WHERE datname = '$Database'").Trim()
if ($dbFound -eq "1") { throw "数据库已存在，为避免重复导入而停止：$Database" }
Invoke-Psql $AdminUrl "CREATE DATABASE \"$Database\";"

$escapedPassword = $AppPassword.Replace("'", "''")
$roleFound = (& $Psql -X -q -t -A -d $AdminUrl -c "SELECT 1 FROM pg_roles WHERE rolname = '$AppUser'").Trim()
if ($roleFound -eq "1") {
    Invoke-Psql $AdminUrl "ALTER ROLE \"$AppUser\" LOGIN PASSWORD '$escapedPassword';"
} else {
    Invoke-Psql $AdminUrl "CREATE ROLE \"$AppUser\" LOGIN PASSWORD '$escapedPassword';"
}

$dbUrl = "postgresql://$AppUser`:$AppPassword@localhost:$Port/$Database"
foreach ($file in $files) {
    $path = Join-Path $sqlRoot $file
    if (-not (Test-Path -LiteralPath $path)) { throw "缺少 SQL 文件：$path" }
    & $Psql -X -v ON_ERROR_STOP=1 -q -d $dbUrl -f $path
    if ($LASTEXITCODE -ne 0) { throw "导入失败：$file" }
}

Invoke-Psql $dbUrl "GRANT USAGE ON SCHEMA \"Production\", \"Sales\" TO \"$AppUser\"; GRANT SELECT ON ALL TABLES IN SCHEMA \"Production\", \"Sales\" TO \"$AppUser\"; ALTER DEFAULT PRIVILEGES IN SCHEMA \"Production\", \"Sales\" GRANT SELECT ON TABLES TO \"$AppUser\";"
Write-Output "AdventureWorks 已导入：$Database"
Write-Output "设置环境变量：ADVENTUREWORKS_DATABASE_URL=postgresql://$AppUser:<password>@localhost:$Port/$Database"
