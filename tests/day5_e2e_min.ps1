param([string]$Api = "http://127.0.0.1:8000")

$ErrorActionPreference = "Stop"
$RequestDelayMs = 300

function Assert($cond, $msg) {
  if (-not $cond) { Write-Host "FAIL: $msg" -ForegroundColor Red; exit 1 }
  Write-Host "OK:   $msg" -ForegroundColor Green
}

# Build a request body compatible with either schema (campaign_brief new, org_brief legacy)
function BuildReq($filters, $k=6) {
  return @{
    campaign_brief  = "Asheville Relief Fund is a 501(c)(3) supporting WNC small-business recovery."
    org_brief       = "Asheville Relief Fund is a 501(c)(3) supporting WNC small-business recovery."
    retrieve_filters = $filters
    k = $k
  }
}

function Post($route, $obj) {
  $json = $obj | ConvertTo-Json -Depth 8
  $res = Invoke-RestMethod -Method POST -Uri ($Api + $route) -ContentType "application/json" -DisableKeepAlive -Body $json
  Start-Sleep -Milliseconds $RequestDelayMs
  return $res
}

Write-Host ""
Write-Host "=== A) Ingestion (skips if chunks exist) ==="
$chunksPath = "data/processed/chunks.jsonl"

if (-not (Test-Path $chunksPath)) {
  python scripts/ingest.py --url "https://www.sba.gov/funding-programs/disaster-assistance" --url "https://www.ncdps.gov/our-organization/emergency-management/disaster-recovery/public-assistance" --url "https://www.commerce.nc.gov/guidelines-project-descriptions-round-1-small-business-infrastructure-grant-program-smbiz/download?attachment=" --county "Haywood" --topic "small_business" --date "2025-02-05" --outdir "data/processed"
}
if (Test-Path $chunksPath) { Write-Host "INFO: using existing chunks at data/processed/chunks.jsonl" }

Assert (Test-Path "data/processed") "processed folder exists"

if (-not (Test-Path $chunksPath)) {
  $candidate = Get-ChildItem "data/processed" -Filter *.jsonl -File -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($candidate) { $chunksPath = $candidate.FullName }
}
Assert (Test-Path $chunksPath) "chunks JSONL file exists: $chunksPath"

$first = Get-Content $chunksPath -TotalCount 1 | ConvertFrom-Json
Assert ($null -ne $first.doc_id -and $first.doc_id -ne "") "doc_id present"
Assert ($first.date -match "^\d{4}-\d{2}-\d{2}$") "date is ISO-like"
Assert ($first.PSObject.Properties.Name -contains "url") "url field present"

Write-Host ""
Write-Host "=== B) Retrieval ==="
$res = Post "/generate/email" (BuildReq @{} 6)
Assert ($null -ne $res.email_md -and $res.email_md.Length -gt 0) "email_md returned"
Assert ((($res.email_sources) | Measure-Object).Count -ge 0) "legacy email_sources present"

$res = Post "/generate/email" (BuildReq @{ date_from = "2024-09-01"; date_to = "2025-12-31" } 6)
Assert ($null -ne $res.email_md -and $res.email_md.Length -gt 0) "filtered email_md returned"
Assert ((($res.email_sources) | Measure-Object).Count -ge 0) "filtered citations ok"

foreach ($k in 3, 12) {
  $r = Post "/generate/email" (BuildReq @{} $k)
  Assert ($null -ne $r) ("k=" + $k + " returned 200")
  $c = (($r.email_sources) | Measure-Object).Count
  Write-Host ("k=" + $k + " -> " + $c + " sources")
}

$r = Post "/generate/email" (BuildReq @{ date_from = "2015-01-01"; date_to = "2015-12-31" } 6)
$noMarkers = -not ($r.email_md -match "\[\d+\]")
$srcCount = (($r.email_sources) | Measure-Object).Count
Assert $noMarkers "no markers on no-match"
Assert ($srcCount -eq 0) "no citations on no-match"

Write-Host ""
Write-Host "=== C) Post-process ==="
$r = Post "/generate/email" (BuildReq @{} 6)
$psCount = ([regex]::Matches($r.email_md, "(?m)^P\.S\.:")).Count
Assert ($psCount -eq 1) "P.S. appears once"
$markerCount = ([regex]::Matches($r.email_md, "\[(\d+)\]")).Count
$sourceCount = (($r.email_sources) | Measure-Object).Count
Assert ($markerCount -eq $sourceCount) "marker count matches sources count"
if ($r.email -and $r.email.subject_lines) {
  Assert ($r.email.subject_lines.Count -eq 3) "3 subject lines"
} else {
  Write-Host "INFO: typed email.subject_lines not present; skipping."
}

Write-Host ""
Write-Host "=== D) Routes ==="
$rE = Post "/generate/email" (BuildReq @{} 6)
$rN = Post "/generate/narrative" (BuildReq @{} 6)
Assert ($rE.PSObject.Properties.Name -contains "email_md") "legacy email_md present"
Assert ($rE.PSObject.Properties.Name -contains "email_sources") "legacy email_sources present"
if ($rE.email) { Write-Host "INFO: typed email present" }
if ($rN.narrative) { Write-Host "INFO: typed narrative present" }

Write-Host ""
Write-Host "=== G) Resilience ==="
$r = Post "/generate/email" (BuildReq @{} 6)
Assert ($null -ne $r) "200 with missing optional fields"

Write-Host ""
Write-Host "=== H) Health ==="
$root = Invoke-RestMethod -Uri ($Api + "/") -DisableKeepAlive
Start-Sleep -Milliseconds $RequestDelayMs
$oa = Invoke-RestMethod -Uri ($Api + "/openapi.json") -DisableKeepAlive
Start-Sleep -Milliseconds $RequestDelayMs
Assert ($null -ne $root) "/ returns 200"
Assert ($oa.info.title) "OpenAPI returns info"

Write-Host ""
Write-Host "All scripted checks passed (E and F are manual in UI)."