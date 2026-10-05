[CmdletBinding()]
param(
    [string]$Target = 'generic-comfyui',
    [switch]$List,
    [switch]$Print,
    [switch]$Push,
    [string]$Tag,
    [string]$HFTokenFile
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$catalog = Get-Content -LiteralPath (Join-Path $projectRoot 'build-catalog.json') -Raw | ConvertFrom-Json
if ($List) {
    $catalog.targets | Select-Object name, service, purpose | Format-Table -AutoSize
    return
}
$entry = $catalog.targets | Where-Object { $_.name -eq $Target }
if (-not $entry) { throw "Unknown target '$Target'. Run scripts/build.ps1 -List for supported choices." }
if ($Print -and $Push) { throw 'Choose -Print to preview or -Push to publish, not both.' }
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCommand) { throw 'Docker Buildx is required. Add docker.exe to PATH.' }
$arguments = @('buildx', 'bake', '-f', 'docker-bake.hcl', $Target, '--progress', 'plain')
if ($Tag) { $arguments += @('--set', "$Target.tags=$Tag") }
if ($HFTokenFile) {
    if ($entry.service -ne 'ltx25') { throw '-HFTokenFile is supported for LTX workers. See docs/builds.md for image-workflow authentication.' }
    $tokenPath = (Resolve-Path -LiteralPath $HFTokenFile).Path.Replace('\', '/')
    $arguments += @('--var', "EXTRA_HF_TOKEN_FILE=$tokenPath")
} elseif ($entry.service -eq 'ltx25' -and -not $Print -and -not $env:HF_TOKEN -and -not $env:EXTRA_HF_TOKEN_FILE) {
    throw 'Set HF_TOKEN privately or supply -HFTokenFile for the complete LTX model bundle.'
}
if ($Print) { $arguments += '--print' }
elseif ($Push) { $arguments += '--push' }
else { $arguments += '--load' }
Push-Location -LiteralPath $projectRoot
try {
    & $dockerCommand.Source @arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Bake failed with exit code $LASTEXITCODE." }
} finally { Pop-Location }
