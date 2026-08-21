param(
    [string]$DbRoot = "stalzone-database",
    [string]$Region = "RU",
    [string]$Lang = "ru",
    [string]$OutputDir = "data/staldata_cache/artifact_matrix",
    [string]$ApiBase = "https://staldata.org/api/stalcraft/public",
    [int]$MaxArtifacts = 0,
    [int]$DelayMs = 75
)

$ErrorActionPreference = "Stop"

$artifactRoot = Join-Path $DbRoot "$Lang/items/artefact"
if (-not (Test-Path -LiteralPath $artifactRoot)) {
    throw "Artifact directory not found: $artifactRoot"
}

$regionValue = $Region.ToUpperInvariant()
$regionDir = Join-Path $OutputDir $regionValue
New-Item -ItemType Directory -Force -Path $regionDir | Out-Null

$files = Get-ChildItem -LiteralPath $artifactRoot -Recurse -File -Filter "*.json" |
    Where-Object { $_.FullName -notmatch "\\_variants\\" } |
    Sort-Object FullName

if ($MaxArtifacts -gt 0) {
    $files = $files | Select-Object -First $MaxArtifacts
}

$index = 0
$failures = @()
foreach ($file in $files) {
    $index += 1
    $item = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $itemId = [string]$item.id
    if ([string]::IsNullOrWhiteSpace($itemId)) {
        continue
    }

    $encodedId = [Uri]::EscapeDataString($itemId)
    $url = "$ApiBase/market/artifact-matrix?region=$regionValue&item_id=$encodedId"
    $outFile = Join-Path $regionDir "$itemId.json"
    $tempFile = "$outFile.tmp"

    Write-Host "[$index/$($files.Count)] $itemId"
    & curl.exe --silent --show-error --location --fail --noproxy "*" --max-time 30 $url --output $tempFile
    if ($LASTEXITCODE -ne 0) {
        $failures += [pscustomobject]@{
            item_id = $itemId
            path = $file.FullName
            exit_code = $LASTEXITCODE
        }
        Remove-Item -LiteralPath $tempFile -ErrorAction SilentlyContinue
    } else {
        Move-Item -LiteralPath $tempFile -Destination $outFile -Force
    }

    if ($DelayMs -gt 0) {
        Start-Sleep -Milliseconds $DelayMs
    }
}

$failurePath = Join-Path $regionDir "_failures.json"
if ($failures.Count -eq 0) {
    "[]" | Set-Content -LiteralPath $failurePath -Encoding UTF8
} else {
    $failures | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $failurePath -Encoding UTF8
}

if ($failures.Count -gt 0) {
    Write-Warning "Completed with $($failures.Count) failures. See $failurePath"
    exit 2
}

Write-Host "Fetched $($files.Count) artifact matrix files to $regionDir"
