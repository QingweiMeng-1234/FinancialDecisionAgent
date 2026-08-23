import { createStep, createWorkflow } from "@mastra/core/workflows";

import { workflowStateSchema } from "./workflow.js";

export function createHistoricalProjectionWorkflow() {
  const stage = (number: number) =>
    createStep({
      id: `import-stage-${number}`,
      inputSchema: workflowStateSchema,
      outputSchema: workflowStateSchema,
      execute: async ({ inputData }) => inputData,
    });

  return createWorkflow({
    id: "theme-chokepoint-historical-projection",
    inputSchema: workflowStateSchema,
    outputSchema: workflowStateSchema,
  })
    .then(stage(1))
    .then(stage(2))
    .then(stage(3))
    .then(stage(4))
    .then(stage(5))
    .then(stage(6))
    .then(stage(7))
    .commit();
}
