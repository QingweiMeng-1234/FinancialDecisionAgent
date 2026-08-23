import { createHash } from "node:crypto";

import { failure, type Failure } from "./errors.js";
import {
  decodeFailureResponse,
  manifestEnvelopeSchema,
  manifestIsSemanticallyValid,
  runListEnvelopeSchema,
  type ManifestData,
} from "./schemas.js";
import type {
  StageProjection,
  WorkflowProjectionState,
} from "./workflow.js";

type PythonPort = {
  call(tool: string, input: Record<string, unknown>): Promise<unknown>;
};

export type HistoricalProjectionWorkflowPort = {
  get(mastraRunId: string): Promise<unknown | null>;
  create(mastraRunId: string, input: WorkflowProjectionState): Promise<void>;
};

export function historicalProjectionRunId(pythonRunId: string): string {
  const digest = createHash("sha256").update(pythonRunId).digest("hex").slice(0, 24);
  return `tc-import-${digest}`;
}

export async function importHistoricalRuns(
  python: PythonPort,
  workflow: HistoricalProjectionWorkflowPort,
  requestedRunIds?: readonly string[],
): Promise<
  | {
      ok: true;
      data: {
        imported: Array<{
          pythonRunId: string;
          mastraRunId: string;
          imported: true;
        }>;
      };
    }
  | Failure
> {
  try {
    const runIds = requestedRunIds
      ? [...requestedRunIds]
      : await listPythonRunIds(python);
    if (isFailure(runIds)) return runIds;
    if (
      runIds.length !== new Set(runIds).size ||
      runIds.some((runId) => !runId || runId.trim() !== runId)
    ) {
      return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
    }
    const imported = [];
    for (const pythonRunId of runIds) {
      const mastraRunId = historicalProjectionRunId(pythonRunId);
      const existing = await workflow.get(mastraRunId);
      if (existing) {
        if (
          typeof existing !== "object" ||
          !("pythonRunId" in existing) ||
          existing.pythonRunId !== pythonRunId ||
          !("imported" in existing) ||
          existing.imported !== true
        ) {
          return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
        }
      } else {
        const raw = await python.call("theme_chokepoint_get_artifacts", {
          run_id: pythonRunId,
        });
        const pythonFailure = decodeFailureResponse(raw);
        if (pythonFailure) return pythonFailure;
        const parsed = manifestEnvelopeSchema.safeParse(raw);
        if (
          !parsed.success ||
          !manifestIsSemanticallyValid(parsed.data.data, pythonRunId)
        ) {
          return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
        }
        await workflow.create(
          mastraRunId,
          stateFromManifest(mastraRunId, parsed.data.data),
        );
      }
      imported.push({ pythonRunId, mastraRunId, imported: true as const });
    }
    return { ok: true, data: { imported } };
  } catch (error) {
    const retryable =
      typeof error === "object" &&
      error !== null &&
      "retryable" in error &&
      error.retryable === true;
    return failure("MCP_TOOL_FAILURE", retryable);
  }
}

async function listPythonRunIds(
  python: PythonPort,
): Promise<string[] | Failure> {
  const raw = await python.call("theme_chokepoint_list_runs", {});
  const pythonFailure = decodeFailureResponse(raw);
  if (pythonFailure) return pythonFailure;
  const parsed = runListEnvelopeSchema.safeParse(raw);
  return parsed.success
    ? [...parsed.data.data.run_ids]
    : failure("MCP_RESPONSE_SCHEMA_MISMATCH");
}

function stateFromManifest(
  mastraRunId: string,
  manifest: ManifestData,
): WorkflowProjectionState {
  const byStage = new Map(manifest.stages.map((receipt) => [receipt.stage, receipt]));
  const incomplete = new Set([
    "INCOMPLETE_BUDGET_EXHAUSTED",
    "COMPANY_ASSESSMENT_INCOMPLETE",
  ]);
  const stageResults: StageProjection[] = Array.from(
    { length: 7 },
    (_, index) => {
      const stage = index + 1;
      const receipt = byStage.get(stage);
      return receipt
        ? {
            stage,
            state: incomplete.has(receipt.output_status)
              ? "incomplete" as const
              : "completed" as const,
            inputStatus: receipt.input_status,
            outputStatus: receipt.output_status,
            outcome: receipt.outcome,
            artifactIds: [...receipt.artifact_ids],
            completedAt: receipt.completed_at,
          }
        : {
            stage,
            state: "not_entered" as const,
            inputStatus: null,
            outputStatus: null,
            outcome: null,
            artifactIds: [],
            completedAt: null,
          };
    },
  );
  return {
    mastraRunId,
    pythonRunId: manifest.run_id,
    imported: true,
    pythonStatus: manifest.final_status,
    contractId: manifest.contract_id,
    executableContractId: manifest.executable_contract_id,
    executableContractSha256: manifest.executable_contract_sha256,
    originalCreatedAt: manifest.created_at,
    originalUpdatedAt: manifest.updated_at,
    stageResults,
  };
}

function isFailure(value: string[] | Failure): value is Failure {
  return "ok" in value && value.ok === false;
}
