import { failure, type Failure } from "./errors.js";
import {
  decodeFailureResponse,
  decodeRunResponse,
  manifestEnvelopeSchema,
  manifestIsSemanticallyValid,
  type ManifestData,
} from "./schemas.js";
import type {
  ProjectionResult,
  ProjectionUpdate,
  StageProjection,
} from "./workflow.js";

type PythonPort = {
  call(tool: string, input: Record<string, unknown>): Promise<unknown>;
};

type Correlation = { mastraRunId: string; pythonRunId: string };

const expectedStatuses: Record<number, readonly string[]> = {
  2: ["READY_FOR_SUPPLY_CHAIN"],
  3: ["SUPPLY_CHAIN_GRAPH_READY"],
  4: ["CHOKEPOINT_ASSESSMENT_READY"],
  5: ["COMPANY_ASSESSMENT_READY"],
  6: ["PERSISTENT_RESEARCH_READY"],
  7: ["PERSISTENT_RESEARCH_READY", "MONITORING_READY"],
};

const incompleteStatuses = new Set([
  "INCOMPLETE_BUDGET_EXHAUSTED",
  "COMPANY_ASSESSMENT_INCOMPLETE",
]);

export async function projectPythonStage(
  python: PythonPort,
  correlation: Correlation,
  stage: number,
): Promise<ProjectionResult> {
  if (!Number.isInteger(stage) || stage < 1 || stage > 7) {
    return failure("INVALID_ARGUMENT");
  }
  try {
    const runRaw = await python.call("theme_chokepoint_get_run", {
      run_id: correlation.pythonRunId,
    });
    const runFailure = decodeFailureResponse(runRaw);
    if (runFailure) return runFailure;
    const decoded = decodeRunResponse(runRaw, correlation.pythonRunId);
    if (decoded.kind === "unknown-status") {
      return failure("UNKNOWN_PYTHON_STATUS");
    }
    if (decoded.kind === "schema-mismatch") {
      return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
    }

    if (stage === 1) {
      const manifest = await readManifest(
        python,
        correlation.pythonRunId,
        decoded.data.status,
      );
      return isFailure(manifest)
        ? manifest
        : projectionResult(manifest, stage);
    }

    const accepted = expectedStatuses[stage] ?? [];
    if (accepted.includes(decoded.data.status)) {
      if (stage === 6) {
        const current = await readManifest(
          python,
          correlation.pythonRunId,
          decoded.data.status,
        );
        if (isFailure(current)) return current;
        if (current.stages.some((item) => item.stage === 6)) {
          return projectionResult(current, stage);
        }
      }
      const advancedRaw = await python.call("theme_chokepoint_advance_stage", {
        run_id: correlation.pythonRunId,
        expected_stage: stage,
        expected_status: decoded.data.status,
        idempotency_key: `${correlation.mastraRunId}:${correlation.pythonRunId}:stage-${stage}`,
      });
      const advanceFailure = decodeFailureResponse(advancedRaw);
      if (advanceFailure) return advanceFailure;
      const advanced = manifestEnvelopeSchema.safeParse(advancedRaw);
      if (
        !advanced.success ||
        !manifestIsSemanticallyValid(
          advanced.data.data,
          correlation.pythonRunId,
        )
      ) {
        return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
      }
      return projectionResult(advanced.data.data, stage);
    }

    const manifest = await readManifest(
      python,
      correlation.pythonRunId,
      decoded.data.status,
    );
    if (isFailure(manifest)) return manifest;
    const receipt = manifest.stages.find((item) => item.stage === stage);
    if (!receipt && decoded.data.nextStage !== null) {
      return failure("PYTHON_STATUS_MISMATCH");
    }
    return projectionResult(manifest, stage);
  } catch (error) {
    const retryable =
      typeof error === "object" &&
      error !== null &&
      "retryable" in error &&
      error.retryable === true;
    return failure("MCP_TOOL_FAILURE", retryable);
  }
}

async function readManifest(
  python: PythonPort,
  pythonRunId: string,
  expectedStatus: string,
): Promise<ManifestData | Failure> {
  const raw = await python.call("theme_chokepoint_get_artifacts", {
    run_id: pythonRunId,
  });
  const pythonFailure = decodeFailureResponse(raw);
  if (pythonFailure) return pythonFailure;
  const parsed = manifestEnvelopeSchema.safeParse(raw);
  if (
    !parsed.success ||
    !manifestIsSemanticallyValid(
      parsed.data.data,
      pythonRunId,
      expectedStatus as Parameters<typeof manifestIsSemanticallyValid>[2],
    )
  ) {
    return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
  }
  return parsed.data.data;
}

function projectionResult(
  manifest: ManifestData,
  stage: number,
): { ok: true; update: ProjectionUpdate } {
  const receipt = manifest.stages.find((item) => item.stage === stage);
  const projection: StageProjection = receipt
    ? {
        stage,
        state: incompleteStatuses.has(receipt.output_status)
          ? "incomplete"
          : "completed",
        inputStatus: receipt.input_status,
        outputStatus: receipt.output_status,
        outcome: receipt.outcome,
        artifactIds: [...receipt.artifact_ids],
        completedAt: receipt.completed_at,
      }
    : {
        stage,
        state: "not_entered",
        inputStatus: null,
        outputStatus: null,
        outcome: null,
        artifactIds: [],
        completedAt: null,
      };
  return {
    ok: true,
    update: {
      pythonStatus: manifest.final_status,
      contractId: manifest.contract_id,
      executableContractId: manifest.executable_contract_id,
      executableContractSha256: manifest.executable_contract_sha256,
      originalCreatedAt: manifest.created_at,
      originalUpdatedAt: manifest.updated_at,
      projection,
    },
  };
}

function isFailure(value: ManifestData | Failure): value is Failure {
  return "ok" in value && value.ok === false;
}
