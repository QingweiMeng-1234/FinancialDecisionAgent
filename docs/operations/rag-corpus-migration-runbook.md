# RAG Corpus Migration Backup and Rollback Runbook

## 1. Purpose and scope

This runbook is the operational control for work package 1 in
`docs/architecture/rag-ingestion-reliability-remediation-plan.md`: inventory,
manifest, migration dry-run/apply/verify, and recoverability.

It applies only to the Financial Agent RAG corpus:

- SQLite article database;
- canonical article-content directory;
- current Chroma persistence directory and the later, separate Chroma v2
  generation;
- migration manifests, verification reports, and the code/config snapshot
  required to interpret them.

This document does **not** authorize another migration or activation. On
2026-08-15 the local workspace migration, immutable generation build, and
control-pointer activation described in Section 1.1 were completed under the
user's explicit authorization. The commands below remain templates for a
separately reviewed operator run against any other workspace or deployed
service.

## 1.1 Current assessed state and hard boundary (2026-08-15)

The local workspace now has a migrated canonical corpus and an activated,
verified generation:

- canonical database: `data/rag_corpus_v2_20260815/news_articles.db`;
- canonical content root: `data/rag_corpus_v2_20260815/data/articles`;
- `rag_corpus_migration.py verify` reports `status=verified`, `audit_rows=482`,
  `checked_ready_articles=421`, and zero issues;
- its reviewed `plan_id` is
  `79b92bfa0dba29cba22331bad51edb339496c47569a5b6d766c086e06406d0ee`;
- Chroma root: `data/rag_index_v2_20260815`;
- control database: `data/runtime/index_generation_control.db`;
- active generation: `news-generation-20260815-v1`;
- active collection: `news_articles_v2_20260815_v1`;
- canonical snapshot fingerprint:
  `be6f4018900f611dc89b57f345f6f45b4691680032e6eac4fff1d1b6eb801e99`;
- index configuration fingerprint:
  `19a3d54a4ca6de2acdf702d3ca206297c31c3113cfd0851f1ea01523728b61d5`;
- verified manifest: 421 articles and 2,810 observed chunks with zero issues.

The default MCP/CLI runtime configuration points to those stable v2 paths. The
canonical DB was backed up before publisher-date apply to
`.tmp/rag-canonical-pre-publication-apply-20260815-2300.db` (source SHA-256:
`11BEBED7C8F253F1619818C54795E0A289E3E298570800D07E4778EBB3D442BF`). The
backfill evidence is retained at:

- `.tmp/publication-backfill-ready-dry-run-full.json`;
- `.tmp/publication-backfill-ready-apply-full.json`.

The dry-run reported 400 successes and 21 failures. The authorized local apply
reported 405 successes and 16 failures. Consequently, the 421 ready rows are
now split into 405 `publisher_metadata` rows with verified UTC-aware
`source_published_at` values and 16 `legacy_unverified` rows. The latter are
not assigned a synthetic current date and remain excluded from time-bounded
"latest" retrieval. The publication receipt check found 405 receipts for 405
distinct article IDs with zero receipt mismatch; canonical verification then
reported `audit_rows=482`, `checked_ready_articles=421`, and zero issues.

On 2026-08-16, after a later refresh attempt had added canonical rows without
activating a successor generation, the canonical database contained 578 rows,
507 ready rows, 405 `publisher_metadata` rows, 96 `source_metadata` rows, and
77 `legacy_unverified` rows (16 of them ready). The still-active 421-article
generation therefore resolves to 405 publisher-verified dates and 16
legacy-unverified dates. A read-only repeat publisher-metadata dry-run over
those 16 ready rows is recorded at
`.tmp/publication-backfill-remaining-dry-run-20260816.json`: seven rows (article
IDs 312, 362, 379, 494, 533, 624, and 657) now expose one unambiguous,
offset-aware publisher `datePublished`; nine remain unresolved (five HTTP 403,
one HTTP 429, one missing value, one naive value, and one invalid value). The
repeat run did not write the canonical database. Apply the seven receipts only
after writers and any successor build are frozen, using the explicit guarded
`--apply` workflow and a fresh backup; keep the other nine excluded. URL date
segments, prose datelines, the legacy `published_at`, fetch time, and HTTP
`Last-Modified` are not acceptable substitutes for publisher publication
metadata.

After the required writer freeze and backup, the reviewed seven-row apply is
targeted explicitly so that a newly recovered eighth publisher cannot silently
expand the mutation scope:

```powershell
$repo = (Resolve-Path '.').Path
$env:PYTHONPATH = "$repo\src;$repo\.venv\Lib\site-packages"
$env:PYTHONIOENCODING = 'utf-8'
python backfill_publication_metadata.py `
  --db-path data/rag_corpus_v2_20260815/news_articles.db `
  --ready-only --apply --delay-seconds 0.1 --timeout-seconds 30 `
  --max-body-bytes 2000000 `
  --allow-host www.businessinsider.com --allow-host fortune.com `
  --article-id 312 --article-id 362 --article-id 379 `
  --article-id 494 --article-id 533 --article-id 624 --article-id 657 `
  --report-json .tmp/publication-backfill-reviewed-seven-apply.json
```

If all reviewed publisher pages remain available and unchanged, the expected
postcondition is `applied_count=7`, 412 receipts for 412 distinct article IDs,
412 `publisher_metadata` rows, nine ready `legacy_unverified` rows, and 70
`legacy_unverified` rows overall. A smaller applied count is a safe partial
result, not authority to fill missing dates from weaker evidence. Reconcile the
receipt IDs and guarded row values before building the successor generation.

A local request-pinned reader smoke test resolved the active generation with
421 eligible articles and zero generation/canonical exclusions. With the
default 30-day UTC window, four publisher-confirmed rows were eligible and the
query returned three results. This proves date filtering is fail closed for the
16 unknown/legacy dates; it does **not** establish the financial relevance or
recency quality of the currently returned news.

The original `news_articles.db` and `chroma_data` still exist and were not
overwritten or deleted. `.tmp/rag-generation-control-20260815-v1.db` preserves
the verified control database from before the pointer activation. It is
recovery evidence, not an operator-safe rollback command.

The local MCP was started after the activation change. The host MCP initialize
request to `127.0.0.1:8877/mcp` returned HTTP 200; Docker reached the same
process through `host.docker.internal:8877/mcp` and also completed initialize
with HTTP 200. The negotiated protocol was `2025-06-18`; the server identified
itself as `financial-agent 1.27.2`, and `tools/list` succeeded. This is a
host/container transport and protocol handshake, not a deployed-service,
scheduled-worker, or user-visible retrieval proof.

The MCP production coordinator now freezes the complete canonical snapshot,
builds a unique successor collection, independently verifies article/chunk
manifests, and activates only after that verification and a successful lease
heartbeat. The activation is therefore conditional and pointer-based: a failed
or late-heartbeat candidate cannot switch `corpus_index_state`; an already
active generation is not modified in place. An independent lease watchdog
renews during blocking construction work and invalidates a candidate if renewal
fails.

The latest live MCP refresh was `run_9acdd945ff18480ea89d33ae07d58ab7` using
the seven-day NewsAPI `everything` request. It returned `status=failed`,
`total_inputs=0`, `source_errors=2`, `total_articles=482`, and no generation
proof. No successor was built or activated, so the active pointer remains
`news-generation-20260815-v1`. Do **not** describe this collection failure as
resolved: its durable child-retry targets/outcomes and any eventual retry,
generation proof, activation, and post-activation retrieval evidence still
need to be appended to this runbook when observed.

Production retry semantics are retry-first and collection-targeted: successful
observations are retained, while failed source/batch targets are scheduled with
durable Retry-After data rather than blindly rerunning the root pipeline. The
parent completion gate requires its baseline, cumulative observations, latest
manifest, attempts, and collection outcomes before a successor generation can
be considered complete. Grounded non-insufficient answers require at least one
valid inline evidence citation.

## 1.2 Command classification

Every command in this document is labelled as one of the following. Do not
promote a command to a safer class merely because its input happens to be a
copy.

| Label | Meaning | Examples in this runbook |
| --- | --- | --- |
| **READ-ONLY** | Must not intentionally change any input or output. It may fail closed. | `verify`, read-only SQLite queries, file-manifest comparisons. |
| **DRY-RUN** | Reads an approved frozen source and writes a proposed plan/report only. | `rag_corpus_migration.py dry-run`. |
| **ISOLATED MUTATION** | Creates new artifacts outside live roots. It still changes data and needs staging approval. | migration `apply`; `rag_index_generation.py build`. |
| **LIVE MUTATION / APPROVAL REQUIRED** | Changes a service-visible pointer, configuration, process, or live data. | activation, rollback, writer freeze/unfreeze, deployment/configuration changes. |

`rag_index_generation.py` has only a `build` command. It deliberately creates
a new generation DB and Chroma persistence root and reports
`active_generation_changed=false`; it is not an activation CLI. The current
`activate_generation(...)` function is a runtime-library primitive, not an
operator-safe deployment command. Do not call it ad hoc with `python -c`, a
REPL, or a SQLite shell.

The source-of-truth and activation rules are those in the remediation plan:
canonical content is verified before index construction; an index generation
is activated only after complete manifest validation; and the active
generation is the SQLite `corpus_index_state` pointer, not a collection name
or a filesystem-path convention.

## 2. Non-negotiable safety rules

1. Freeze all writers before creating an inventory, backup, dry-run, apply,
   verification, activation, or rollback candidate. This includes MCP/CLI
   `refresh_news`, theme-research ingestion, backfill, summary/index repair,
   scheduled jobs, and any process that can write `news_articles.db`, article
   content, or Chroma.
2. Record evidence before mutation: Git SHA and dirty state, tool/config
   version, database hash, content manifest, Chroma manifest, timestamps, and
   operator identity/change ticket.
3. Git is not a corpus backup. A Git commit cannot safely restore an ignored,
   modified, locally generated database, article file, or vector store.
4. Create independent, immutable-by-convention copies of the DB, canonical
   content, and Chroma directory before any apply. Store the copies outside
   the live corpus path and preferably on a separately managed volume or
   backup system.
5. Never use `git reset --hard`, `git clean`, `Remove-Item`, or deletion of a
   live corpus directory as a migration or rollback procedure. This runbook
   does not delete local data.
6. Run `dry-run`, then `apply` only in staging, then `verify`. Production-like
   activation is a separate approved operation. Chroma v2 is built and
   validated as a separate generation; it is never an in-place overwrite of
   v1.
7. A failed validation is a stop condition, not an instruction to retry with
   weaker checks. Preserve evidence, leave the active pointer unchanged, and
   investigate from the backed-up copies.

## 3. Roles, inputs, and stop/go gates

| Phase | Required input | Operator action | Go condition | Stop condition |
| --- | --- | --- | --- | --- |
| Preflight | approved change, release SHA, maintenance window | freeze writers and record process state | no writer/lease remains active | active writer cannot be stopped or identified |
| Snapshot | frozen live corpus | create DB/content/Chroma manifests and independent copies | all hashes/manifests complete | any copy/hash mismatch or missing input |
| Dry-run | snapshot manifest + migration config | classify only; write proposed actions | no mutation, deterministic report | unresolved identity/path/hash issue |
| Staging apply | independent backup copy | migrate schema/content metadata only in staging | apply report matches dry-run scope | tool changes unplanned live data or reports an error |
| Staging verify | staging DB/content | verify canonical candidate coverage and integrity | zero unexplained candidate gaps | path/hash/version/identity mismatch |
| Chroma v2 | verified staging canonical snapshot | build new generation, then verify manifest | full coverage/hash/chunk/config checks pass | any missing, duplicate, stale, or mixed-generation chunk |
| Activation | approved verified generation | atomically switch SQLite active-generation pointer | read-only smoke checks pass | pointer/config mismatch or retrieval integrity error |
| Rollback | previous verified generation + backups | switch pointer back, or make an isolated recovery candidate | old verified generation reads correctly | backups are unavailable or do not verify |

The operator must record the decision and evidence at every gate. A status of
`partial`, `failed`, `in_progress`, or any active refresh lease blocks
migration apply and activation.

## 4. Required artifacts

Create one timestamped run directory, for example
`D:\financial-agent-rag-backups\2026-08-15T103000Z`, containing:

- `preflight.json`: approved change ID, operator, timestamps, writer-freeze
  evidence, Git SHA, Git dirty-state summary, and migration tool/config
  version/hash;
- `db-manifest.json` and a read-only copy of `news_articles.db` (plus SQLite
  `-wal` and `-shm` files if they exist while the database is quiescent);
- `articles-manifest.jsonl` with relative path, byte length, SHA-256, and
  article ID parsed from the filename; and an independent copy of the article
  content root;
- `chroma-v1-manifest.jsonl` with relative path, byte length, and SHA-256,
  plus an independent copy of the Chroma v1 persistence root;
- `dry-run-report.json`, `apply-report.json`, and `verify-report.json` from
  the migration tool, each carrying the same `snapshot_id`;
- for v2, `generation-manifest.json`, `article-index-manifest.jsonl`, and the
  v2 validation report; and
- `activation-record.json` or `rollback-record.json`, as applicable.

The `snapshot_id` is generated once per frozen corpus and is included in every
later report. A report derived from a different snapshot cannot authorize an
activation.

The migration manifest's `source_database_logical_sha256` is the SHA-256 of a
read-only SQLite logical dump (schema plus all table rows) from one consistent
SQLite snapshot. It is deliberately not a hash of only `news_articles.db`:
committed changes may reside in `news_articles.db-wal`. During `apply`, the
tool rechecks that logical source snapshot before copying, then hashes the
fresh staged SQLite backup before changing its schema. Either mismatch is a
stop condition; do not treat a matching main-DB file hash as sufficient.

## 5. PowerShell preflight and independent-backup templates

The following are templates, deliberately not commands to execute during this
documentation work. Fill paths explicitly for the approved environment. They
copy data into a new, empty backup run directory and do not delete or modify
the live corpus.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = 'C:\Users\Lenovo\Documents\github\Financial Agent\Financial Agent'
$dbPath = Join-Path $repoRoot 'news_articles.db'
$articlesRoot = Join-Path $repoRoot 'data\articles'
$chromaV1Root = Join-Path $repoRoot 'chroma_data'
$backupBase = 'D:\financial-agent-rag-backups'
$snapshotId = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHHmmssZ')
$runRoot = Join-Path $backupBase $snapshotId

# Precondition: writers are frozen and refresh leases have been checked.
New-Item -ItemType Directory -Path $runRoot -ErrorAction Stop | Out-Null

$gitSha = git -C $repoRoot rev-parse HEAD
$gitStatus = git -C $repoRoot status --short
$preflight = [ordered]@{
  snapshot_id = $snapshotId
  captured_at_utc = (Get-Date).ToUniversalTime().ToString('o')
  git_sha = $gitSha.Trim()
  git_status_short = @($gitStatus)
  writer_freeze_confirmed = $true # replace only with ticketed evidence
  db_path = $dbPath
  articles_root = $articlesRoot
  chroma_v1_root = $chromaV1Root
}
$preflight | ConvertTo-Json -Depth 5 |
  Set-Content -LiteralPath (Join-Path $runRoot 'preflight.json') -Encoding utf8

function New-TreeManifest {
  param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string]$OutputPath)
  Get-ChildItem -LiteralPath $Root -File -Recurse |
    Sort-Object FullName |
    ForEach-Object {
      $relative = $_.FullName.Substring($Root.TrimEnd('\').Length).TrimStart('\')
      [ordered]@{
        relative_path = $relative
        bytes = $_.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
      } | ConvertTo-Json -Compress
    } | Set-Content -LiteralPath $OutputPath -Encoding utf8
}

# Hash before copy and after copy. Do not treat Git as this backup.
Get-FileHash -Algorithm SHA256 -LiteralPath $dbPath |
  ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runRoot 'db-manifest.json') -Encoding utf8
New-TreeManifest -Root $articlesRoot -OutputPath (Join-Path $runRoot 'articles-manifest.jsonl')
New-TreeManifest -Root $chromaV1Root -OutputPath (Join-Path $runRoot 'chroma-v1-manifest.jsonl')

Copy-Item -LiteralPath $dbPath -Destination (Join-Path $runRoot 'news_articles.db') -ErrorAction Stop
Copy-Item -LiteralPath $articlesRoot -Destination (Join-Path $runRoot 'articles') -Recurse -ErrorAction Stop
Copy-Item -LiteralPath $chromaV1Root -Destination (Join-Path $runRoot 'chroma-v1') -Recurse -ErrorAction Stop

Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $runRoot 'news_articles.db')
New-TreeManifest -Root (Join-Path $runRoot 'articles') -OutputPath (Join-Path $runRoot 'articles-copy-manifest.jsonl')
New-TreeManifest -Root (Join-Path $runRoot 'chroma-v1') -OutputPath (Join-Path $runRoot 'chroma-v1-copy-manifest.jsonl')
```

Before this template is approved for a live SQLite database, the migration
tooling must either checkpoint and verify a quiescent database or include any
existing `news_articles.db-wal` and `news_articles.db-shm` files in the
snapshot manifest. Do not copy a changing SQLite database and call it a
recoverable snapshot.

## 6. Dry-run, staging apply, and verify protocol

The implemented entrypoint is the standard-library-only
`rag_corpus_migration.py` wrapper. It has explicit `dry-run`, `apply`, and
`verify` subcommands. `apply` reads the reviewed manifest, rechecks source
drift, uses SQLite backup, and creates a new staging directory; it never
updates the source DB/content and refuses an existing output directory.

```powershell
# Read-only: source is the frozen independent snapshot, not the live corpus.
$migrationTool = Join-Path $repoRoot 'rag_corpus_migration.py'
$planPath = Join-Path $runRoot 'dry-run-report.json'
$dryRunSummaryPath = Join-Path $runRoot 'dry-run-summary.json'

$dryRunSummary = & py -3 $migrationTool dry-run `
  --db-path (Join-Path $runRoot 'news_articles.db') `
  --content-root (Join-Path $runRoot 'articles') `
  --chroma-dir (Join-Path $runRoot 'chroma-v1') `
  --collection-name 'news_articles' `
  --snapshot-id $snapshotId `
  --manifest-out $planPath
$dryRunExit = $LASTEXITCODE
$dryRunSummary | Set-Content -LiteralPath $dryRunSummaryPath -Encoding utf8
if ($dryRunExit -notin @(0, 2)) {
  throw "dry-run failed with exit code $dryRunExit"
}

# Exit 2 means the audit completed and found integrity issues. Review every
# manifest entry and obtain the migration approval before continuing.
$reviewedPlan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json

# The output must not exist. The tool creates the staged DB/content copy.
$stagingRoot = "D:\financial-agent-rag-staging\$snapshotId"
if (Test-Path -LiteralPath $stagingRoot) {
  throw "staging output already exists: $stagingRoot"
}
$applySummary = & py -3 $migrationTool apply `
  --plan $planPath `
  --output-dir $stagingRoot `
  --confirm-plan-id $reviewedPlan.plan_id
if ($LASTEXITCODE -ne 0) { throw 'staging apply failed' }
$applySummary | Set-Content -LiteralPath (Join-Path $runRoot 'apply-report.json') -Encoding utf8

$verifySummary = & py -3 $migrationTool verify --output-dir $stagingRoot
if ($LASTEXITCODE -ne 0) { throw 'staging verify failed' }
$verifySummary | Set-Content -LiteralPath (Join-Path $runRoot 'verify-report.json') -Encoding utf8
```

Verification must fail closed unless all of the following hold:

- every `canonical_verified_migration_candidate` has a readable content file,
  path relative to the staged `data/articles` content root, matching active SHA-256,
  and unambiguous article identity;
- every staged valid-ready row has `active_content_sha256` equal to both its
  manifest SHA-256 and the SHA-256 of its hash-addressed file, with
  `content_validation_status=verified` and `index_status=pending`;
- staging deliberately invalidates all derived artifacts: every row has
  `indexed_content_sha256=NULL`, `summary_status=pending`, `summary=NULL`,
  and `summary_content_sha256=NULL`. A new index or summary may be produced
  only after it is explicitly bound to the staged active content hash;
- every source-ready row that fails canonical validation is persisted in the
  staged DB as `content_status=failed` and
  `content_validation_status=quarantined`, with its timestamp, stable reason,
  safe source-relative path (when one exists), expected/observed hashes, and
  reviewed `plan_id`; the audit table alone is not sufficient evidence of a
  quarantine;
- no candidate is duplicated, orphaned, or silently omitted;
- `apply` reports only operations proposed by the matching dry-run;
- old-read compatibility remains available until the new read path passes its
  own acceptance tests; and
- the evidence bundle binds the release SHA in `preflight.json`; the migration
  manifest and summaries carry `snapshot_id`, reviewed `plan_id` (the plan/config
  integrity hash), and `manifest_schema_version` (the tool contract version).

No `apply` may be performed against the production-like live corpus in this
work package. An eventual live migration needs a separately approved release
plan and an additional fresh frozen backup.

## 7. Verified generation procedure: isolated build first

Do not point a successor generation at the legacy `chroma_data` directory,
reuse `news_articles`, create output below the staging directory, or delete v1
chunks. A generation build is **ISOLATED MUTATION**, even though it must not
change a live reader pointer.

The implemented `rag_index_generation.py build` preflight verifies the staging
corpus again, requires both output paths not to exist, rejects protected source
and live-Chroma paths, and then creates (1) a new generation-control SQLite DB
and (2) a new Chroma persistence root. It validates its own generation/chunk
proof and leaves `corpus_index_state` empty. A failure may intentionally retain
these new candidate artifacts for diagnosis; do not reuse or overwrite them.

The following is an approved-environment template only. It must be run against
an independent frozen staging copy, not the repository `.tmp` path used for
today's evidence, and only after an approval for isolated build work:

```powershell
# ISOLATED MUTATION / staging approval required.  All output paths must be new.
$indexTool = Join-Path $repoRoot 'rag_index_generation.py'
$verifiedStage = 'D:\financial-agent-rag-staging\<snapshot-id>'
$candidateRoot = 'D:\financial-agent-rag-generations\<generation-id>'
$generationDb = Join-Path $candidateRoot 'index-generations.sqlite3'
$candidateChroma = Join-Path $candidateRoot 'chroma'
$generationId = '<immutable-generation-id>'
$collectionName = '<new-collection-name>'
$corpusId = 'news'
$approvedEmbeddingArtifact = '<pinned-local-embedding-artifact>'
$chunkSize = <approved-positive-integer>
$chunkOverlap = <approved-non-negative-integer-less-than-chunkSize>

if (Test-Path -LiteralPath $generationDb) { throw 'candidate DB already exists' }
if (Test-Path -LiteralPath $candidateChroma) { throw 'candidate Chroma root already exists' }
if ($candidateChroma -eq (Join-Path $repoRoot 'chroma_data')) {
  throw 'candidate Chroma root must not be the legacy live root'
}

& py -3 $indexTool build `
  --staging-dir $verifiedStage `
  --generation-db $generationDb `
  --persist-root $candidateChroma `
  --generation-id $generationId `
  --collection-name $collectionName `
  --corpus-id $corpusId `
  --embedding-artifact $approvedEmbeddingArtifact `
  --chunk-size $chunkSize `
  --chunk-overlap $chunkOverlap
if ($LASTEXITCODE -ne 0) { throw 'candidate generation did not verify' }
```

The build JSON must say `status=verified` and `active_generation_changed=false`.
Archive that JSON, the generated `generation-input-manifest.json`, generation
DB hash, candidate-Chroma tree manifest, exact embedding artifact, chunk config
fingerprint, stage `plan_id`, snapshot fingerprint, and tool/release SHA in the
same evidence bundle. `--allow-model-download` is prohibited by default; a
separate network/model-supply approval is required before it may be used.

## 8. Candidate read-only verification and serving smoke checks

These checks are **READ-ONLY** with respect to the candidate artifacts. They
must happen before an activation request. They are not a live serving check:
the current MCP/CLI configuration is not yet proven to point at the candidate.

1. Re-run `rag_corpus_migration.py verify --output-dir $verifiedStage` and
   compare its `plan_id`, source logical DB hash, ready/quarantine counts, and
   eligible snapshot fingerprint with the build record.
2. Open the candidate generation DB with SQLite `mode=ro`, then use
   `ActiveGenerationResolver.open_verified_candidate($generationId)`. It must
   return `status=verified`, exact generation/corpus/collection IDs, a valid
   chunk proof with zero issues, and manifests whose SHA-256 values correspond
   to the frozen canonical staging DB/content root.
3. Build an eligibility snapshot from that same staged canonical DB/content
   root. The candidate's article IDs and indexed hashes must equal this
   snapshot; no legacy `index_status` or v1 IDs may be used as evidence.
4. Before initializing a Chroma reader, assert that `$candidateChroma` already
   exists, is the expected candidate root, and contains the expected
   persistence files. Hash the tree before and after the test. The reader uses
   only collection `get`/`count`/`query` APIs, but Chroma's
   `PersistentClient(path=...)` can create a directory/database when passed a
   missing path. It is API-query-only, **not** filesystem-read-only by itself.
5. Construct `ChromaGenerationReader` only with that verified candidate and
   run a small fixed set of approved retrieval probes. Record returned
   generation ID, article ID, content SHA, chunk index, and result count. A
   probe result is accepted only if every returned metadata value agrees with
   the pinned manifest and eligibility snapshot. An empty result is a test
   outcome, not permission to query legacy Chroma as a fallback.

At least two people should independently review the immutable evidence bundle:
one confirms corpus/generation identity and one confirms retrieval results. Any
missing candidate path, hash drift, metadata mismatch, model-load failure,
reader exception, or post-check tree-hash difference blocks activation.

## 9. Local activation record and future approval gate

Activation is **LIVE MUTATION / APPROVAL REQUIRED**. It is a deployment and
control-plane change, not the final step of the build command.

For the authorized 2026-08-15 local repair, the verified
`news-generation-20260815-v1` row was changed to `active` and
`corpus_index_state('news')` was set to that generation in
`data/runtime/index_generation_control.db`. Read-only reconciliation after the
operation confirmed the pointer, 421 manifest entries, and the 2,810-chunk
verification record. The local serving smoke described in Section 1.1 then
queried that exact generation. No legacy DB or Chroma directory was replaced.

That operation does not authorize an arbitrary pointer change or prove a
separate deployment. The production MCP coordinator is explicitly authorized
to perform a *verified successor activation*: it switches the pointer only
after a complete frozen-snapshot build, manifest/chunk readback, and a final
successful lease heartbeat. The normal offline/default coordinator still
returns a proof without activation. Any other activation path, including a
manual deployment or rollback, must pass the controls below.

This does not turn activation into a general-purpose operator CLI. The pointer
is the only serving-state mutation; the coordinator never overwrites the old
collection, canonical files, or legacy Chroma. A failed build, failed proof,
or failed/late heartbeat leaves the prior active pointer in place and retains
the candidate only as diagnostic evidence.

Do not request approval until a separately reviewed activation controller and
release plan provide all of the following:

- exact live locations for a durable control DB, canonical DB/content root,
  immutable candidate Chroma root, and `ServingCorpusConfig` used by the MCP
  and CLI processes;
- proof that the release configuration points at those locations and will not
  silently fall back to legacy `ChromaVectorStore`;
- a fresh writer freeze, independent live backup, live preflight inventory,
  and the exact previous active generation ID (or an explicitly approved
  first-generation recovery strategy);
- the reviewed candidate evidence from Sections 7--8, including matching
  `plan_id`, canonical snapshot/config fingerprints, and zero verification
  issues;
- an atomic, audited pointer operation guarded by the expected previous
  pointer and candidate generation ID; and
- named approver, operator, maintenance window, success criteria, rollback
  trigger, and a service-level smoke-test plan.

The local control plane now has one verified active generation, but it has no
second verified generation representing the legacy serving state. Therefore a
pointer-only rollback to legacy remains unavailable. Restoring legacy service
configuration would be a separate recovery change; it must not be simulated by
pointing the v2 control DB at an unverified or absent generation.

After all prerequisites and explicit approval exist, the activation controller
must: freeze writers; re-run its read-only preflight; record the exact old
pointer; make the single guarded pointer/configuration cutover; and emit an
`activation-record.json` containing before/after generation IDs, control-DB
hashes, service release SHA/config fingerprint, operator/approver, UTC times,
and the candidate evidence IDs. It must not copy over legacy DB/content/Chroma
directories or delete an old collection.

## 10. Post-activation verification and rollback

Immediately after the approved controller reports success, run only
**READ-ONLY** checks before unfreezing writers:

1. Resolve the active generation from the deployed control DB with
   `ActiveGenerationResolver.resolve($corpusId)`; it must be `active` and
   equal the approved generation ID.
2. Verify the deployed service configuration points to the same control DB,
   canonical corpus root, and candidate Chroma root recorded in the activation
   record.
3. Run the approved MCP/CLI retrieval smoke probes through the deployed
   process, preserving request/generation identifiers. Confirm no legacy
   collection fallback, only eligible hash-bound articles, and fail-closed
   behavior for an invalid control-plane condition.
4. Compare the candidate DB/Chroma/content manifests to their pre-activation
   hashes. Record result counts and any errors; a successful process start,
   HTTP 200, or `202` alone is not retrieval/user-visible proof.

Rollback is also **LIVE MUTATION / APPROVAL REQUIRED**. If any activation
smoke check fails, keep writers frozen and use the reviewed activation
controller to atomically restore the exact recorded previous verified
generation/configuration. Then repeat the read-only checks above against that
predecessor and write `rollback-record.json`. Do not repair the incident by
deleting, reverse-editing, or overwriting either Chroma collection.

If the candidate was the first generation and there is no verified predecessor
with a tested serving configuration, pointer reversal is impossible. The only
safe action is to keep the new generation out of service (or restore the
separately approved prior release configuration), preserve all evidence, and
open an incident. Database/content restoration is a separate emergency change:
stage the independent backup into a new isolated directory, verify it, and get
explicit approval before replacing any environment.

## 11. Failure boundaries and rollback

| Failure point | Required containment | Rollback/recovery action |
| --- | --- | --- |
| Writer found during preflight | stop before snapshot | obtain a fresh freeze; discard the attempted run as non-authoritative |
| Snapshot copy/manifest mismatch | do not dry-run/apply | keep evidence, create a new independent copy after correcting the cause |
| Dry-run classification discrepancy | no apply | correct tooling/contract, retain frozen source and rerun dry-run |
| Staging schema/content apply failure | no v2 build or activation | abandon staging candidate; original frozen backup and live corpus remain unchanged |
| v2 build or manifest validation failure | leave v1 active | retain v2 as failed diagnostic artifact; repair in a new successor generation |
| Activation smoke-check failure | stop writers; do not mutate old collection | atomically point `corpus_index_state.active_generation_id` back to the recorded previous verified generation, then verify read-only retrieval |
| DB/content corruption discovered | preserve evidence and freeze writers | make a new isolated recovery candidate from the independent backup; validate it before any separately authorized restoration |

Rollback normally means an active-pointer reversal, not copying old files over
new files and not reverse-editing a Chroma collection. This preserves both
generations and enables later diagnosis. If the database itself must be
recovered, stage the backup into a new isolated directory first, verify its
database/content/manifest relationship, and obtain explicit incident approval
before any environment replacement. This runbook provides no overwrite or
deletion command for that exceptional operation.

## 12. Completion record

Close a migration run only when the record contains:

- the approved change ID and freeze/unfreeze times;
- snapshot ID and location of all three independent backups;
- matching before/after manifests and DB hashes;
- dry-run, staging apply, and verify reports;
- v2 generation ID and full validation report, if v2 was built;
- activation/rollback pointer and deployment configuration before/after, or a
  documented reason that no activation occurred; and
- test and read-only smoke-check results, with known exclusions/failures.

Unfreeze writers only after the selected active generation and its SQLite
pointer have passed verification. Resuming writers is not evidence that a
migration succeeded; the immutable manifests and activation record are the
evidence.
