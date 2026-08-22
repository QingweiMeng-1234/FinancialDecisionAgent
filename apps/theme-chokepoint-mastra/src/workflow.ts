import { createStep, createWorkflow } from "@mastra/core/workflows";
import { z } from "zod";

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

const continuePythonRun = createStep({
  id: "continue-python-run",
  inputSchema: correlationSchema,
  outputSchema: correlationSchema,
  execute: async ({ inputData }) => inputData,
});

export function createM0Workflow() {
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
