[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [int]$MaxArticleId = 230,
    [int]$MinRelevant = 3,
    [ValidateSet('fixed', 'relevant_count')]
    [string]$EvaluationTopKMode = 'relevant_count',
    [string]$BaseOutputDir = '',
    [switch]$CompanyOnly,
    [switch]$NoProgress
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($BaseOutputDir)) {
    $timestamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
    $BaseOutputDir = Join-Path $repoRoot "reports\retrieval_eval_relevance_split_$timestamp"
} elseif (-not [System.IO.Path]::IsPathRooted($BaseOutputDir)) {
    $BaseOutputDir = Join-Path $repoRoot $BaseOutputDir
}

$filters = @('all', 'direct', 'indirect')
$commonArgs = @(
    'run_retrieval_eval.py',
    'evaluate',
    '--max-article-id', $MaxArticleId,
    '--min-relevant', $MinRelevant,
    '--evaluation-top-k-mode', $EvaluationTopKMode
)

if ($CompanyOnly) {
    $commonArgs += '--company-only'
}

if ($NoProgress) {
    $commonArgs += '--no-show-progress'
}

New-Item -ItemType Directory -Force -Path $BaseOutputDir | Out-Null

$manifestPath = Join-Path $BaseOutputDir 'run_manifest.txt'
$manifestLines = @(
    "started_at=$(Get-Date -Format o)",
    "base_output_dir=$BaseOutputDir",
    "max_article_id=$MaxArticleId",
    "min_relevant=$MinRelevant",
    "evaluation_top_k_mode=$EvaluationTopKMode",
    "company_only=$($CompanyOnly.IsPresent)",
    "no_progress=$($NoProgress.IsPresent)",
    "filters=$($filters -join ',')"
)
$manifestLines | Set-Content -Path $manifestPath -Encoding utf8

Write-Host "Base output dir: $BaseOutputDir"

foreach ($filter in $filters) {
    $outputDir = Join-Path $BaseOutputDir $filter
    $args = @($commonArgs + @(
        '--output-dir', $outputDir,
        '--relevance-type-filter', $filter
    ))

    $displayCommand = 'python ' + ($args -join ' ')
    Write-Host ""
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting $filter evaluation..."
    Write-Host $displayCommand

    if ($PSCmdlet.ShouldProcess($outputDir, "Run $filter retrieval evaluation")) {
        & python @args
        if ($LASTEXITCODE -ne 0) {
            throw "Evaluation failed for filter '$filter' with exit code $LASTEXITCODE."
        }
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Finished $filter evaluation."
    }
}

$doneLine = "finished_at=$(Get-Date -Format o)"
Add-Content -Path $manifestPath -Value $doneLine -Encoding utf8

Write-Host ""
Write-Host "All evaluations completed."
Write-Host "Reports root: $BaseOutputDir"
