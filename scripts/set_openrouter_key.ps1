# Asks for your OpenRouter key without showing it, and writes it into the project's .env.
# The key is never printed and never stored in your command history.
# Usage (from the project root):  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\set_openrouter_key.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$envPath = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $envPath)) { Copy-Item -LiteralPath (Join-Path $root '.env.example') -Destination $envPath }

$secure = Read-Host 'Paste your OpenRouter key (input is hidden), then press Enter' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

if (-not $key.StartsWith('sk-or-')) {
    Write-Host 'That does not look like an OpenRouter key (it should start with sk-or-). Nothing was changed.' -ForegroundColor Yellow
    exit 1
}

$lines = [IO.File]::ReadAllLines($envPath)
$found = $false
for ($i = 0; $i -lt $lines.Length; $i++) {
    if ($lines[$i] -match '^\s*#?\s*NSM_OPENROUTER_API_KEY\s*=') { $lines[$i] = "NSM_OPENROUTER_API_KEY=$key"; $found = $true }
    elseif ($lines[$i] -match '^\s*NSM_LLM_MODE\s*=') { $lines[$i] = 'NSM_LLM_MODE=openrouter' }
}
if (-not $found) { $lines += "NSM_OPENROUTER_API_KEY=$key" }
[IO.File]::WriteAllLines($envPath, $lines, (New-Object Text.UTF8Encoding $false))

$len = $key.Length
Remove-Variable key, secure
Write-Host "Saved: key ($len characters) written to $envPath and NSM_LLM_MODE set to openrouter." -ForegroundColor Green
Write-Host 'Go back to the chat and say continue.' -ForegroundColor Green
