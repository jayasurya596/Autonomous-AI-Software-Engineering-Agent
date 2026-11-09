$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvironmentFile = Join-Path $ProjectRoot ".env.cloudflare"
$WebEnvironmentFile = Join-Path $ProjectRoot ".env.production.local"
$WranglerConfig = Join-Path $ProjectRoot "wrangler.jsonc"

function Read-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required configuration file is missing: $Path"
    }
    $Values = @{}
    foreach ($Line in Get-Content -LiteralPath $Path) {
        $Trimmed = $Line.Trim()
        if (-not $Trimmed -or $Trimmed.StartsWith("#")) { continue }
        if ($Trimmed -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { continue }
        $Key = $Matches[1]
        $Value = $Matches[2].Trim()
        if ($Value.Length -ge 2 -and (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or ($Value.StartsWith("'") -and $Value.EndsWith("'")))) {
            $Value = $Value.Substring(1, $Value.Length - 2)
        }
        $Values[$Key] = $Value
    }
    return $Values
}

function Require-Value([hashtable]$Values, [string]$Name) {
    if (-not $Values.ContainsKey($Name) -or [string]::IsNullOrWhiteSpace([string]$Values[$Name])) {
        throw "Set $Name in .env.cloudflare before deploying."
    }
    if ([string]$Values[$Name] -match '^replace-with-') {
        throw "Replace the placeholder value for $Name in .env.cloudflare."
    }
}

$Runtime = Read-DotEnv $EnvironmentFile
$Web = Read-DotEnv $WebEnvironmentFile
foreach ($Name in @(
    "FIREBASE_PROJECT_ID",
    "FIREBASE_ADMIN_CREDENTIALS_FILE",
    "DATABASE_URL",
    "GITHUB_CREDENTIALS_ENCRYPTION_KEY"
)) {
    Require-Value $Runtime $Name
}
foreach ($Name in @("VITE_FIREBASE_API_KEY", "VITE_FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_PROJECT_ID", "VITE_FIREBASE_APP_ID")) {
    Require-Value $Web $Name
}
if ($Runtime["FIREBASE_PROJECT_ID"] -ne $Web["VITE_FIREBASE_PROJECT_ID"]) {
    throw "FIREBASE_PROJECT_ID must match VITE_FIREBASE_PROJECT_ID."
}
$GoogleAuthEnabled = [string]$Web["VITE_FIREBASE_GOOGLE_AUTH_ENABLED"]
if ([string]::IsNullOrWhiteSpace($GoogleAuthEnabled)) {
    $GoogleAuthEnabled = "true"
}
$BuildEnvironment = [ordered]@{
    VITE_FIREBASE_API_KEY = [string]$Web["VITE_FIREBASE_API_KEY"]
    VITE_FIREBASE_AUTH_DOMAIN = [string]$Web["VITE_FIREBASE_AUTH_DOMAIN"]
    VITE_FIREBASE_PROJECT_ID = [string]$Web["VITE_FIREBASE_PROJECT_ID"]
    VITE_FIREBASE_APP_ID = [string]$Web["VITE_FIREBASE_APP_ID"]
    VITE_FIREBASE_GOOGLE_AUTH_ENABLED = $GoogleAuthEnabled
    VITE_FIREBASE_PREVIEW_ONLY = "false"
    VITE_FIREBASE_AUTH_EMULATOR_HOST = ""
    VITE_CLOUD_REPOSITORY_STORAGE = "r2"
}
$DatabaseUrl = [string]$Runtime["DATABASE_URL"]
if ($DatabaseUrl -notmatch '^postgresql\+psycopg://[^@/]+@(?<host>[^/:?]+)(?::\d+)?/[^?]+(?:\?.*)?$') {
    throw "DATABASE_URL must be a PostgreSQL psycopg connection URL for a cloud database."
}
if ($Matches["host"] -in @("localhost", "127.0.0.1", "::1")) {
    throw "DATABASE_URL must not point to a local database."
}
if ($DatabaseUrl -notmatch '(?:\?|&)sslmode=require(?:&|$)') {
    throw "DATABASE_URL must require TLS with sslmode=require."
}

$AdminPath = [string]$Runtime["FIREBASE_ADMIN_CREDENTIALS_FILE"]
if (-not [System.IO.Path]::IsPathRooted($AdminPath)) {
    $AdminPath = Join-Path $ProjectRoot $AdminPath
}
if (-not (Test-Path -LiteralPath $AdminPath -PathType Leaf)) {
    throw "The Firebase Admin service-account JSON file was not found at the configured path."
}
$AdminJson = Get-Content -LiteralPath $AdminPath -Raw
try {
    $AdminCredential = $AdminJson | ConvertFrom-Json
} catch {
    throw "The Firebase Admin service-account file is not valid JSON."
}
if ($AdminCredential.project_id -ne $Runtime["FIREBASE_PROJECT_ID"]) {
    throw "The Firebase Admin service-account project does not match FIREBASE_PROJECT_ID."
}

Push-Location $ProjectRoot
$TemporarySecretsFile = $null
$PreviousBuildEnvironment = @{}
try {
    foreach ($Name in $BuildEnvironment.Keys) {
        $PreviousBuildEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
        [Environment]::SetEnvironmentVariable($Name, [string]$BuildEnvironment[$Name], "Process")
    }

    $Identity = & npx --yes wrangler@4.158.0 whoami --config $WranglerConfig 2>&1
    if ($LASTEXITCODE -ne 0 -or ($Identity -join "`n") -match "not authenticated") {
        throw "Cloudflare CLI is not signed in. Run 'npx --yes wrangler@4.158.0 login' in a terminal, finish the browser authorization, then run this script again."
    }

    Push-Location (Join-Path $ProjectRoot "frontend")
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "The frontend dependency install failed." }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "The frontend build failed." }
    } finally {
        Pop-Location
    }

    $Secrets = [ordered]@{
        FIREBASE_PROJECT_ID = [string]$Runtime["FIREBASE_PROJECT_ID"]
        FIREBASE_ADMIN_CREDENTIALS_JSON = $AdminJson
        DATABASE_URL = [string]$Runtime["DATABASE_URL"]
        GITHUB_CREDENTIALS_ENCRYPTION_KEY = [string]$Runtime["GITHUB_CREDENTIALS_ENCRYPTION_KEY"]
    }
    $TemporarySecretsFile = [System.IO.Path]::GetTempFileName()
    $Json = ConvertTo-Json -InputObject $Secrets -Depth 8 -Compress
    [System.IO.File]::WriteAllText($TemporarySecretsFile, $Json, [System.Text.UTF8Encoding]::new($false))

    & npx --yes wrangler@4.158.0 deploy --config $WranglerConfig --secrets-file $TemporarySecretsFile
    if ($LASTEXITCODE -ne 0) { throw "Cloudflare deployment failed. Review Wrangler's error above." }
} finally {
    if ($TemporarySecretsFile -and (Test-Path -LiteralPath $TemporarySecretsFile)) {
        Remove-Item -LiteralPath $TemporarySecretsFile -Force
    }
    foreach ($Name in $BuildEnvironment.Keys) {
        if ($PreviousBuildEnvironment.ContainsKey($Name)) {
            $PreviousValue = $PreviousBuildEnvironment[$Name]
            [Environment]::SetEnvironmentVariable($Name, $PreviousValue, "Process")
        }
    }
    Pop-Location
}
