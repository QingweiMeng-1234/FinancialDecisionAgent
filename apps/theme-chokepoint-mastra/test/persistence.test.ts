import { mkdir, mkdtemp } from "node:fs/promises";
import { join } from "node:path";

import { expect, it } from "vitest";

import { LibSqlM0StateStore } from "../src/state.js";

const correlation = {
  mastraRunId: "mastra-1",
  pythonRunId: "python-1",
  workflowId: "theme-chokepoint-m0",
};
const receipt = {
  runId: "python-1",
  confirmedAnchorIds: ["anchor-2", "anchor-1"],
  confirmedBy: "owner",
  confirmedAt: "2026-08-23T11:00:00+00:00",
};

it("persists correlation, exact receipt CAS, and one cross-runtime continue owner", async () => {
  const tempRoot = join(process.cwd(), ".tmp");
  await mkdir(tempRoot, { recursive: true });
  const directory = await mkdtemp(join(tempRoot, "m0-libsql-"));
  const database = join(directory, "state.sqlite");
  const first = await LibSqlM0StateStore.open(database);
  await first.createCorrelation(correlation);
  await expect(first.putConfirmation("mastra-1", receipt)).resolves.toBe("stored");
  await first.close();

  const reopened = await LibSqlM0StateStore.open(database);
  expect(await reopened.getCorrelation("mastra-1")).toEqual(correlation);
  expect(await reopened.getConfirmation("mastra-1")).toEqual(receipt);
  await expect(reopened.putConfirmation("mastra-1", receipt)).resolves.toBe("same");
  await expect(
    reopened.putConfirmation("mastra-1", {
      ...receipt,
      confirmedBy: "other",
    }),
  ).resolves.toBe("conflict");

  const competing = await LibSqlM0StateStore.open(database);
  const owners = await Promise.all([
    reopened.claimContinue("mastra-1"),
    competing.claimContinue("mastra-1"),
  ]);
  expect(owners.filter(Boolean)).toHaveLength(1);
  const owner = owners[0] ? reopened : competing;
  await owner.completeContinue("mastra-1");
  await expect(reopened.claimContinue("mastra-1")).resolves.toBe(false);
  await expect(competing.claimContinue("mastra-1")).resolves.toBe(false);
  await reopened.close();
  await competing.close();
});

it("persists the independent Mastra workflow self-correlation snapshot across reopen", async () => {
  const tempRoot = join(process.cwd(), ".tmp");
  await mkdir(tempRoot, { recursive: true });
  const directory = await mkdtemp(join(tempRoot, "m0-workflow-"));
  const database = join(directory, "state.sqlite");
  const first = await LibSqlM0StateStore.open(database);
  await first.createSnapshot(correlation);
  await first.close();

  const reopened = await LibSqlM0StateStore.open(database);
  await expect(reopened.getSnapshot("mastra-1")).resolves.toEqual(correlation);
  await reopened.close();
});

it("allows one expired continue lease takeover after owner process death", async () => {
  const tempRoot = join(process.cwd(), ".tmp");
  await mkdir(tempRoot, { recursive: true });
  const directory = await mkdtemp(join(tempRoot, "m0-lease-"));
  const database = join(directory, "state.sqlite");
  const first = await LibSqlM0StateStore.open(database);
  const competing = await LibSqlM0StateStore.open(database);
  await first.createCorrelation(correlation);
  await first.putConfirmation("mastra-1", receipt);
  type LeaseStore = {
    claimContinue(
      runId: string,
      owner: string,
      nowMs: number,
      leaseMs: number,
    ): Promise<boolean>;
    completeContinue(runId: string, owner: string): Promise<boolean>;
  };
  const firstLease = first as unknown as LeaseStore;
  const competingLease = competing as unknown as LeaseStore;

  await expect(
    firstLease.claimContinue("mastra-1", "dead-process", 1_000, 100),
  ).resolves.toBe(true);
  await expect(
    competingLease.claimContinue("mastra-1", "live-process", 1_050, 100),
  ).resolves.toBe(false);
  await expect(
    competingLease.claimContinue("mastra-1", "live-process", 1_101, 100),
  ).resolves.toBe(true);
  await expect(
    firstLease.completeContinue("mastra-1", "dead-process"),
  ).resolves.toBe(false);
  await expect(
    competingLease.completeContinue("mastra-1", "live-process"),
  ).resolves.toBe(true);
  await expect(
    firstLease.claimContinue("mastra-1", "third-process", 2_000, 100),
  ).resolves.toBe(false);

  await first.close();
  await competing.close();
});
