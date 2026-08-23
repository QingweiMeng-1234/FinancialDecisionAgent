import { expect, it } from "vitest";

import { createThemeChokepointAgent } from "../src/agent.js";

it("registers one constrained Mastra Agent with lifecycle and projection tools only", async () => {
  const service = {
    async startM0(input: unknown) { return input; },
    async getM0Status(input: unknown) { return input; },
    async getM0PendingAnchors(input: unknown) { return input; },
    async resumeM0(input: unknown) { return input; },
    async getM0Artifacts(input: unknown) { return input; },
    async importHistoricalRuns(input: unknown) { return input; },
  };

  const agent = createThemeChokepointAgent(service);
  const tools = await agent.listTools();

  expect(agent.id).toBe("theme-chokepoint-operator");
  expect(Object.keys(tools).sort()).toEqual([
    "confirmAndResumeThemeResearch",
    "getThemeArtifacts",
    "getThemePendingAnchors",
    "getThemeStatus",
    "importHistoricalThemeRuns",
    "startThemeResearch",
  ]);
  expect(Object.keys(tools).join(" ")).not.toMatch(/database|sqlite|score|evidence|bypass/i);
});
