# Stage 3 Codex Sessions — review and freeze request

Status: READY_FOR_HUMAN_REVIEW

Version: 1.0-candidate.1

Date: 2026-09-07

## Checked documents

| Artifact | SHA-256 |
| --- | --- |
| [PRD](../../docs/product/theme-stage3-codex-sessions-prd-v1.md) | `e5fdf806c83cb56d9fdcfb3c9b912dafdb5f24377104631905ffbbb21f202fae` |
| [System Design / ADR / Migration](../../docs/architecture/theme-stage3-codex-sessions-system-design-v1.md) | `ec36ca0b1cc131cc933851dfdc91d94aac6c03a02e760388ecff7e338cf6ec63` |
| [API / Data Contract](../../docs/api/theme-stage3-codex-sessions-api-v1.md) | `684fea41111c62702034955f00a35f6685bb91a925fa60258764573a056bbeda` |

[Deterministic report](../../.codex/document-architecture.stage3-codex-sessions-v1.report.json): PASS, no mechanical findings. The existing predecessor document configuration also passed; its semantic documents were not modified.

## Fresh read-only review

One fresh reviewer inspected only the three documents and approved user decisions. It had no write/freeze/run authority. No second review was commissioned. The Document Architect accepted all four findings and applied the following corrections; the reviewer did not re-review those corrections.

| ID | Original severity | Evidence in reviewed version | Architect disposition |
| --- | --- | --- | --- |
| CS-DR-01 | P1 | PRD §2/AC-02 described all execution slots; Design §4 counted active turns only | Resolved: PRD and Design define possible-in-flight turn slots including intents/unknown turns; source fetches and lifecycle operations have separately stated bounds. |
| CS-DR-02 | P1 | API cancel operation suggested cleanup while Design §§5–6 required confirmed terminal turns | Resolved: cancelling intent first; unknown termination keeps lane paused, slot occupied and cleanup waiting_terminal; no archive until terminal confirmation. |
| CS-DR-03 | P1 | PRD REQ-08 unknown fallback versus API §3.3 reuse of prior proposal | Resolved: all documents use the same precedence, with proposal_basis_sha256 and validator recheck; changed evidence invalidates old proposals. |
| CS-DR-04 | P2 | Design §6 lacked a complete descendant-visibility rule before archive | Resolved in contract: both archived states, explicit all-source enumeration, complete pagination and parent links; incomplete visibility fails closed. Actual provider visibility is not yet proven. |

Open review findings: P0=0, P1=0, P2=0 after Architect remediation. This is a document recommendation, not an implementation or production certification.

## Verification actually performed

- Alternate feature document checker: PASS after remediation.
- Existing predecessor checker: PASS.
- Public projectConfigSchema: candidate Profile PASS.
- Referenced public Prompt SHA and three document SHAs: exact match; no Prompt content copied.
- Public frozenBundleSchema: candidate correctly rejected on only bundle_status and human_approval.status.
- Registry unchanged and proposed profile ID absent.
- Old registered Profile unchanged: `13cf026df3abf64baa61a65ad06f2de6274f5f9f971e0f42e51792fa5907009a`.
- Old Frozen Bundle unchanged: `8189fa928769db5e9bd535e7e19a8b1ff77944df03357ee977cd0132aca06001`.
- Product runtime files edited in this document turn: none. Coding runs created: zero. Real research sessions/turns created: zero.

## Candidates and exact hashes

- [Product Bundle candidate](../../.agent/theme-stage3-codex-sessions-bundle-v1.candidate.json): `01567a695fdd39913062301b0973a60fc4d1fceb3a18703aedd1ecd3c3e4cf43`.
- Public platform Profile candidate: `C:/Users/Lenovo/Documents/github/local-agent-platform/services/mastra-coding-platform/profiles/financial-agent-theme-stage3-codex-sessions-v1.candidate.json`; SHA `3bc691e8dd7155a2305f28600ad8deb9ca90f540236122d7afccf3be8994e1f1`.
- Public registration proposal: same directory, `financial-agent-theme-stage3-codex-sessions-v1.registration-proposal.json`; SHA `6c915065d0b18345d1d69702b10273f20e9e09ae648795fbc2f4d90b2b0f7604`.
- Registry observed SHA: `b9131ff3cb44d78f57fcb4c9d9bc0f107af69c45129a68b9f3098702e3367af2`; recheck before activation, never overwrite concurrent edits.

## Exact proposed approval actions — NOT executed

1. Approve the three document byte hashes above. Keep those document bytes, including their review-status header; the newly approved manifest records their frozen authority.
2. Preserve the candidate and generate `.agent/theme-stage3-codex-sessions-frozen-bundle-v1.json` from it. Change only bundle_status to FROZEN_FOR_IMPLEMENTATION and human_approval to APPROVED with the real user approval reference. No invented approval text.
3. Generate the new active Profile `financial-agent-theme-stage3-codex-sessions-v1.json` from its candidate; change bundleFile to the approved frozen manifest path. Keep public Prompt and role routing.
4. Apply only the proposed new Registry entry after verifying no conflicting ID/change. Preserve every old entry.
5. Recheck document and new Bundle hashes and public deterministic preflight. Only a READY result plus human authorization permits exactly one LangGraph Coding run at http://localhost:4130/ui.
6. Stop for Document P0/P1. Code P0/P1 remains in the platform Developer loop. At Human Final Confirmation report the platform exact bundle SHA, candidate source snapshot SHA and open P2; never submit GO.

This turn ends before all six actions. Candidate Bundle SHA is NOT a frozen Bundle SHA, and no candidate source snapshot SHA exists yet.

## Evidence limits

Codex App Server is experimental. Real turn cancellation, effective tool restrictions, subscription telemetry, descendant visibility, archive/display-store consistency and actual quota use still require separately authorized bounded smoke evidence. Offline fixtures cannot establish these. No guaranteed dollar savings, research-quality superiority, immediate memory unloading or production readiness is claimed.

