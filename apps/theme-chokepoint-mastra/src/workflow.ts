import { createStep, createWorkflow } from "@mastra/core/workflows";
import { z } from "zod";

import type { Failure } from "./errors.js";

const correlationSchema = z
  .object({
    mastraRunId: z.string().min(1),
    pythonRunId: z.string().min(1),
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

const confirmationSuspendSchema = correlationSchema
  .extend({
    status: z.literal("AWAITING_PRODUCT_CONFIRMATION"),
  })
  .strict();

const confirmationResumeSchema = correlationSchema
  .extend({
    confirmation: confirmationReceiptSchema,
  })
  .strict();

const continueSuspendSchema = correlationSchema
  .extend({
    error: z
      .object({
        code: z.string().min(1),
        message: z.string().min(1),
        retryable: z.boolean(),
      })
      .strict(),
  })
  .strict();

const correlatePythonRun = createStep({
  id: "correlate-python-run",
  inputSchema: correlationSchema,
  outputSchema: correlationSchema,
  execute: async ({ inputData }) => inputData,
});

const suspendForConfirmation = createStep({
  id: "suspend-for-confirmation",
  inputSchema: correlationSchema,
  outputSchema: correlationSchema,
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

export function createM0Workflow(options: {
  continuePythonRun?: (
    correlation: z.infer<typeof correlationSchema>,
  ) => Promise<{ ok: true } | Failure>;
} = {}) {
  const dispatchContinue =
    options.continuePythonRun ?? (async () => ({ ok: true as const }));
  const continuePythonRun = createStep({
    id: "continue-python-run",
    inputSchema: correlationSchema,
    outputSchema: correlationSchema,
    suspendSchema: continueSuspendSchema,
    resumeSchema: correlationSchema,
    execute: async ({ inputData, resumeData, suspend }) => {
      const correlation = resumeData ?? inputData;
      const result = await dispatchContinue(correlation);
      if (!result.ok) {
        return suspend({ ...correlation, error: result.error });
      }
      return correlation;
    },
  });
  return createWorkflow({
    id: "theme-chokepoint-m0",
    inputSchema: correlationSchema,
    outputSchema: correlationSchema,
  })
    .then(correlatePythonRun)
    .then(suspendForConfirmation)
    .then(continuePythonRun)
    .commit();
}
