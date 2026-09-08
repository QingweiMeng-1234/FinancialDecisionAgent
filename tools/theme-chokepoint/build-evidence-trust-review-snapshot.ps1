param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 5)]
    [int]$CycleNumber,
    [ValidateSet("start", "after")]
    [string]$SnapshotPhase = "start"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (git rev-parse --show-toplevel).Trim()
if (-not $repositoryRoot) {
    throw "Git repository root is unavailable"
}
$oldSnapshotPath = Join-Path $repositoryRoot "reports/theme-chokepoint-review-cycle3/target-snapshot-cycle3.json"
$oldSnapshot = Get-Content -LiteralPath $oldSnapshotPath -Raw -Encoding UTF8 | ConvertFrom-Json

$categoryByPath = @{}
foreach ($file in $oldSnapshot.files) {
    if ($file.category -ne "runtime_conformance") {
        $categoryByPath[$file.path] = $file.category
    }
}

$additions = @{
    "docs/adr/0002-theme-chokepoint-evidence-trust-architecture.zh-CN.md" = "governance_docs"
    "docs/product/theme-chokepoint-evidence-trust-remediation-program-v1.zh-CN.md" = "governance_docs"
    "docs/product/theme-chokepoint-evidence-trust-five-cycle-review-program-v1.zh-CN.md" = "governance_docs"
    "src/event_collector/cli/theme_chokepoint.py" = "runtime_source"
    "src/event_collector/theme_chokepoint/fact_verification.py" = "runtime_source"
    "src/event_collector/theme_chokepoint/governance.py" = "runtime_source"
    "src/event_collector/theme_chokepoint/providers/fact_verifier.py" = "runtime_source"
    "src/event_collector/theme_chokepoint/providers/company_clients.py" = "runtime_source"
    "src/event_collector/theme_chokepoint/production.py" = "runtime_source"
    "tests/test_theme_chokepoint_fact_verification.py" = "focused_tests"
    "tests/test_theme_chokepoint_governance.py" = "focused_tests"
    "tests/test_theme_chokepoint_production_composition.py" = "focused_tests"
    "tests/test_theme_chokepoint_source_repository.py" = "focused_tests"
    "tests/test_theme_chokepoint_repository_migration.py" = "focused_tests"
    "tests/test_theme_chokepoint_review_program_validator.py" = "focused_tests"
    "tools/theme-chokepoint/runtime-governance-bundle-v1.json" = "semantic_tools"
    "tools/theme-chokepoint/source-identity-policy-v1.json" = "semantic_tools"
    "tools/theme-chokepoint/source-identity-schema-v1.json" = "semantic_tools"
    "tools/theme-chokepoint/build-evidence-trust-review-snapshot.ps1" = "review_tools"
    "tools/theme-chokepoint/verify-repository-migration-v1.py" = "review_tools"
    "tools/theme-chokepoint/validate-review-program-v1_1.py" = "review_tools"
    "reports/theme-chokepoint-review-cycle2/repeated-escape-rca-cycle2.json" = "predecessor_receipts"
    "reports/theme-chokepoint-review-cycle3/target-snapshot-cycle3.json" = "predecessor_receipts"
    "reports/theme-chokepoint-review-cycle3/reviewer-a-closure-cycle3.json" = "predecessor_receipts"
    "reports/theme-chokepoint-review-cycle3/cycle-receipt-cycle3.json" = "predecessor_receipts"
    "reports/theme-chokepoint-evidence-trust-remediation-v1/program-manifest-v1.json" = "program_receipts"
    "reports/theme-chokepoint-evidence-trust-remediation-v1/program-freeze-receipt-v1.json" = "program_receipts"
    "reports/theme-chokepoint-evidence-trust-remediation-v1/batch-a-progress-001.json" = "program_receipts"
    "reports/theme-chokepoint-evidence-trust-review-v1/review-program-orchestration-protocol-v1.1.json" = "program_receipts"
    "reports/theme-chokepoint-evidence-trust-review-v1/cycle1/process-violation-challenger-skipped-cycle1.json" = "program_receipts"
}
foreach ($entry in $additions.GetEnumerator()) {
    $categoryByPath[$entry.Key] = $entry.Value
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$files = @()
foreach ($relativePath in @($categoryByPath.Keys | Sort-Object)) {
    $absolutePath = Join-Path $repositoryRoot $relativePath
    if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
        throw "Mandatory review target is missing: $relativePath"
    }
    $item = Get-Item -LiteralPath $absolutePath
    $hash = (Get-FileHash -LiteralPath $absolutePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $files += [ordered]@{
        category = $categoryByPath[$relativePath]
        path = $relativePath.Replace("\", "/")
        size_bytes = $item.Length
        sha256 = $hash
    }
}

$inventoryLines = @(
    $files | ForEach-Object { "$($_.path)`t$($_.size_bytes)`t$($_.sha256)" }
)
$inventoryText = $inventoryLines -join "`n"
$sha = New-Object System.Security.Cryptography.SHA256Managed
$inventoryBytes = [System.Text.Encoding]::UTF8.GetBytes($inventoryText)
$inventorySha256 = ([BitConverter]::ToString($sha.ComputeHash($inventoryBytes))).Replace("-", "").ToLowerInvariant()
$snapshotId = "etrp-cycle$CycleNumber-$SnapshotPhase-$inventorySha256"

$statusLines = @(git status --porcelain=v1 -uall)
$statusText = $statusLines -join "`n"
$statusBytes = [System.Text.Encoding]::UTF8.GetBytes($statusText)
$statusSha256 = ([BitConverter]::ToString($sha.ComputeHash($statusBytes))).Replace("-", "").ToLowerInvariant()

$categoryCounts = [ordered]@{}
foreach ($group in @(
    $files |
        ForEach-Object { $_["category"] } |
        Group-Object |
        Sort-Object Name
)) {
    $categoryCounts[$group.Name] = $group.Count
}

$outputPath = Join-Path $repositoryRoot $OutputDirectory
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$inventoryPath = Join-Path $outputPath "target-inventory-cycle$CycleNumber-$SnapshotPhase.tsv"
$snapshotPath = Join-Path $outputPath "target-snapshot-cycle$CycleNumber-$SnapshotPhase.json"
$statusPath = Join-Path $outputPath "git-status-cycle$CycleNumber-$SnapshotPhase.txt"
[System.IO.File]::WriteAllText($inventoryPath, $inventoryText, $utf8NoBom)
[System.IO.File]::WriteAllText($statusPath, $statusText, $utf8NoBom)

$snapshot = [ordered]@{
    schema_version = "theme-chokepoint-evidence-trust-review-snapshot-v1"
    review_program_id = "theme-chokepoint-evidence-trust-review-v1"
    cycle_number = $CycleNumber
    snapshot_phase = $SnapshotPhase
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    snapshot_id = $snapshotId
    inventory_sha256 = $inventorySha256
    snapshot_algorithm = "sha256(sorted UTF-8 lines: relative_path TAB size_bytes TAB file_sha256; LF join; no trailing LF)"
    mandatory_file_count = $files.Count
    category_counts = $categoryCounts
    git_head = (git rev-parse HEAD).Trim()
    git_status_line_count = $statusLines.Count
    git_status_sha256 = $statusSha256
    inventory_receipt = "$OutputDirectory/target-inventory-cycle$CycleNumber-$SnapshotPhase.tsv"
    git_status_receipt = "$OutputDirectory/git-status-cycle$CycleNumber-$SnapshotPhase.txt"
    predecessor_cycle3_snapshot_id = $oldSnapshot.snapshot_id
    predecessor_fix_loop_status = "BLOCKED_REPEATED_ROOT_CAUSE"
    target_frozen_for_review = $true
    files = $files
    proof_boundary = "This is an exact review target only. It does not close findings, prove E2E, authorize GO or supersede historical Golden/Holdout/freeze artifacts."
}
$snapshotJson = $snapshot | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($snapshotPath, $snapshotJson, $utf8NoBom)

[ordered]@{
    snapshot_id = $snapshotId
    mandatory_file_count = $files.Count
    inventory_sha256 = $inventorySha256
    git_status_line_count = $statusLines.Count
    git_status_sha256 = $statusSha256
    snapshot_path = $snapshotPath
} | ConvertTo-Json -Depth 4
