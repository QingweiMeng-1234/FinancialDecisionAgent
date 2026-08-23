import { createStep, createWorkflow } from "@mastra/core/workflows";
import { z } from "zod";

import type { Failure } from "./errors.js";

const correlationSchema = z
  .object({
    mastraRunId: z.string().min(1),
    pythonRunId: z.string().min(1),
  })
  .strict();

const stageProjectionSchema = z
  .object({
    stage: z.number().int().min(1).max(7),
    state: z.enum(["completed", "incomplete", "not_entered"]),
    inputStatus: z.string().nullable(),
    outputStatus: z.string().nullable(),
    outcome: z.string().nullable(),
    artifactIds: z.array(z.string()),
    completedAt: z.string().nullable(),
  })
  .strict();

export const workflowStateSchema = correlationSchema
  .extend({
    imported: z.boolean(),
    pythonStatus: z.string().min(1),
    contractId: z.string().min(1),
    executableContractId: z.string().min(1),
    executableContractSha256: z.string().regex(/^[a-f0-9]{64}$/),
    originalCreatedAt: z.string().min(1),
    originalUpdatedAt: z.string().min(1),
    stageResults: z.array(stageProjectionSchema).length(7),
  })
  .strict();

const confirmationReceiptSchema = z
  .object({
    runId: z.string().trim().min(1),
    confirmedAnchorIds: z
      .array(z.string().trim().min(1))
      .min(1)
      .refine((values) => new Set(values).size === values.length),
    confirmedBy: z.string().trim().min(1),
    confirmedAt: z.iso.datetime({ offset: true }),
  })
  .strict();

const confirmationSuspendSchema = workflowStateSchema
  .extend({ status: z.literal("AWAITING_PRODUCT_CONFIRMATION") })
  .strict();

const confirmationResumeSchema = correlationSchema
  .extend({ confirmation: confirmationReceiptSchema })
  .strict();

const failureSchema = z
  .object({
    code: z.string().min(1),
    message: z.string().min(1),
    retryable: z.boolean(),
  })
  .strict();

const stageSuspendSchema = workflowStateSchema
  .extend({ error: failureSchema })
  .strict();

export type StageProjection = z.infer<typeof stageProjectionSchema>;
export type WorkflowProjectionState = z.infer<typeof workflowStateSchema>;
export type ProjectionUpdate = Omit<
  WorkflowProjectionState,
  "mastraRunId" | "pythonRunId" | "imported" | "stageResults"
> & { projection: StageProjection };
export type ProjectionResult = { ok: true; update: ProjectionUpdate } | Failure;

const correlatePythonRun = createStep({
  id: "correlate-python-run",
  inputSchema: correlationSchema,
  outputSchema: correlationSchema,
  execute: async ({ inputData }) => inputData,
});

function emptyStageResults(): StageProjection[] {
  return Array.from({ length: 7 }, (_, index) => ({
    stage: index + 1,
    state: "not_entered" as const,
    inputStatus: null,
    outputStatus: null,
    outcome: null,
    artifactIds: [],
    completedAt: null,
  }));
}

function mergeUpdate(
  correlation: z.infer<typeof correlationSchema>,
  prior: WorkflowProjectionState | null,
  update: ProjectionUpdate,
): WorkflowProjectionState {
  const stageResults = prior ? [...prior.stageResults] : emptyStageResults();
  stageResults[update.projection.stage - 1] = update.projection;
  return {
    ...correlation,
    imported: false,
    pythonStatus: update.pythonStatus,
    contractId: update.contractId,
    executableContractId: update.executableContractId,
    executableContractSha256: update.executableContractSha256,
    originalCreatedAt: update.originalCreatedAt,
    originalUpdatedAt: update.originalUpdatedAt,
    stageResults,
  };
}

function defaultUpdate(stage: number): ProjectionUpdate {
  const now = new Date(0).toISOString();
  const outputStatus =
    stage === 1
      ? "AWAITING_PRODUCT_CONFIRMATION"
      : stage === 7
        ? "SIGNAL_EXPORT_READY"
        : "READY_FOR_SUPPLY_CHAIN";
  return {
    pythonStatus: outputStatus,
    contractId: "theme-chokepoint-scoring-v1.4",
    executableContractId: "projection-only",
    executableContractSha256: "0".repeat(64),
    originalCreatedAt: now,
    originalUpdatedAt: now,
    projection: {
      stage,
      state: "completed",
      inputStatus: stage === 1 ? null : "READY_FOR_SUPPLY_CHAIN",
      outputStatus,
      outcome: stage === 1 ? "awaiting_confirmation" : "completed",
      artifactIds: [],
      completedAt: now,
    },
  };
}

export function createM0Workflow(options: {
  projectStage1?: (
    correlation: z.infer<typeof correlationSchema>,
  ) => Promise<ProjectionResult>;
  advancePythonStage?: (
    correlation: z.infer<typeof correlationSchema>,
    stage: number,
  ) => Promise<ProjectionResult>;
} = {}) {
  const projectStage1 =
    options.projectStage1 ??
    (async () => ({ ok: true as const, update: defaultUpdate(1) }));
  const advancePythonStage =
    options.advancePythonStage ??
    (async (_correlation, stage) => ({
      ok: true as const,
      update: defaultUpdate(stage),
    }));

  const projectStage1Step = createStep({
    id: "project-stage-1",
    inputSchema: correlationSchema,
    outputSchema: workflowStateSchema,
    execute: async ({ inputData }) => {
      const result = await projectStage1(inputData);
      if (!result.ok) throw new Error(result.error.code);
      return mergeUpdate(inputData, null, result.update);
    },
  });

  const suspendForConfirmation = createStep({
    id: "suspend-for-confirmation",
    inputSchema: workflowStateSchema,
    outputSchema: workflowStateSchema,
    suspendSchema: confirmationSuspendSchema,
    resumeSchema: confirmationResumeSchema,
    execute: async ({ inputData, resumeData, suspend }) => {
      if (resumeData === undefined) {
        return suspend({
          ...inputData,
          status: "AWAITING_PRODUCT_CONFIRMATION",
        });
      }
      if (
        resumeData.mastraRunId !== inputData.mastraRunId ||
        resumeData.pythonRunId !== inputData.pythonRunId ||
        resumeData.confirmation.runId !== inputData.pythonRunId
      ) {
        return suspend({
          ...inputData,
          status: "AWAITING_PRODUCT_CONFIRMATION",
        });
      }
      return inputData;
    },
  });

  const stageNames = {
    2: "stage-2-supply-chain",
    3: "stage-3-chokepoint",
    4: "stage-4-company-mapping",
    5: "stage-5-persistent-research",
    6: "stage-6-monitoring",
    7: "stage-7-signal-export",
  } as const;
  const stageStep = (stage: 2 | 3 | 4 | 5 | 6 | 7) =>
    createStep({
      id: stageNames[stage],
      inputSchema: workflowStateSchema,
      outputSchema: workflowStateSchema,
      suspendSchema: stageSuspendSchema,
      resumeSchema: correlationSchema,
      execute: async ({ inputData, resumeData, suspend }) => {
        if (
          resumeData !== undefined &&
          (resumeData.mastraRunId !== inputData.mastraRunId ||
            resumeData.pythonRunId !== inputData.pythonRunId)
        ) {
          return suspend({
            ...inputData,
            error: {
              code: "RUN_CORRELATION_MISMATCH",
              message: "Run correlation mismatch",
              retryable: false,
            },
          });
        }
        const correlation = {
          mastraRunId: inputData.mastraRunId,
          pythonRunId: inputData.pythonRunId,
        };
        const result = await advancePythonStage(correlation, stage);
        if (!result.ok) return suspend({ ...inputData, error: result.error });
        return mergeUpdate(correlation, inputData, result.update);
      },
    });

  return createWorkflow({
    id: "theme-chokepoint-m0",
    inputSchema: correlationSchema,
    outputSchema: workflowStateSchema,
  })
    .then(correlatePythonRun)
    .then(projectStage1Step)
    .then(suspendForConfirmation)
    .then(stageStep(2))
    .then(stageStep(3))
    .then(stageStep(4))
    .then(stageStep(5))
    .then(stageStep(6))
    .then(stageStep(7))
    .commit();
}
