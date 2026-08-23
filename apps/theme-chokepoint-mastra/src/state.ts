import { createClient, type Client } from "@libsql/client";
import { z } from "zod";

import type { Confirmation } from "./schemas.js";

export type DurableCorrelation = {
  mastraRunId: string;
  pythonRunId: string;
  workflowId: string;
};

const confirmationSchema = z
  .object({
    runId: z.string().min(1),
    confirmedAnchorIds: z.array(z.string().min(1)).min(1),
    confirmedBy: z.string().min(1),
    confirmedAt: z.string().min(1),
  })
  .strict();

export class LibSqlM0StateStore {
  private constructor(private readonly client: Client) {}

  static async open(path: string): Promise<LibSqlM0StateStore> {
    const client = createClient({
      url: path.startsWith("file:") ? path : `file:${path}`,
    });
    const store = new LibSqlM0StateStore(client);
    await client.execute("PRAGMA busy_timeout = 5000");
    await client.execute("PRAGMA journal_mode = WAL");
    await client.execute(`
      CREATE TABLE IF NOT EXISTS m0_runs (
        mastra_run_id TEXT PRIMARY KEY,
        python_run_id TEXT NOT NULL,
        workflow_id TEXT NOT NULL,
        confirmation_json TEXT,
        continue_state TEXT NOT NULL DEFAULT 'idle'
          CHECK (continue_state IN ('idle', 'claimed', 'complete')),
        continue_owner TEXT,
        continue_lease_until_ms INTEGER
      )
    `);
    const columns = await client.execute("PRAGMA table_info(m0_runs)");
    const columnNames = new Set(
      columns.rows.map((row) => String(row.name)),
    );
    if (!columnNames.has("continue_owner")) {
      await client.execute("ALTER TABLE m0_runs ADD COLUMN continue_owner TEXT");
    }
    if (!columnNames.has("continue_lease_until_ms")) {
      await client.execute(
        "ALTER TABLE m0_runs ADD COLUMN continue_lease_until_ms INTEGER",
      );
    }
    await client.execute(`
      CREATE TABLE IF NOT EXISTS m0_workflow_snapshots (
        mastra_run_id TEXT PRIMARY KEY,
        python_run_id TEXT NOT NULL,
        workflow_id TEXT NOT NULL
      )
    `);
    return store;
  }

  async createCorrelation(value: DurableCorrelation): Promise<void> {
    await this.client.execute({
      sql: `
        INSERT INTO m0_runs(mastra_run_id, python_run_id, workflow_id)
        VALUES (?, ?, ?)
        ON CONFLICT(mastra_run_id) DO NOTHING
      `,
      args: [value.mastraRunId, value.pythonRunId, value.workflowId],
    });
    const durable = await this.getCorrelation(value.mastraRunId);
    if (
      !durable ||
      durable.pythonRunId !== value.pythonRunId ||
      durable.workflowId !== value.workflowId
    ) {
      throw new Error("correlation conflict");
    }
  }

  async getCorrelation(mastraRunId: string): Promise<DurableCorrelation | null> {
    const result = await this.client.execute({
      sql: `
        SELECT mastra_run_id, python_run_id, workflow_id
        FROM m0_runs WHERE mastra_run_id = ?
      `,
      args: [mastraRunId],
    });
    const row = result.rows[0];
    if (!row) return null;
    return {
      mastraRunId: String(row.mastra_run_id),
      pythonRunId: String(row.python_run_id),
      workflowId: String(row.workflow_id),
    };
  }

  async createSnapshot(value: DurableCorrelation): Promise<void> {
    await this.client.execute({
      sql: `
        INSERT INTO m0_workflow_snapshots(
          mastra_run_id, python_run_id, workflow_id
        ) VALUES (?, ?, ?)
        ON CONFLICT(mastra_run_id) DO NOTHING
      `,
      args: [value.mastraRunId, value.pythonRunId, value.workflowId],
    });
    const durable = await this.getSnapshot(value.mastraRunId);
    if (
      !durable ||
      durable.pythonRunId !== value.pythonRunId ||
      durable.workflowId !== value.workflowId
    ) {
      throw new Error("workflow snapshot conflict");
    }
  }

  async getSnapshot(mastraRunId: string): Promise<DurableCorrelation | null> {
    const result = await this.client.execute({
      sql: `
        SELECT mastra_run_id, python_run_id, workflow_id
        FROM m0_workflow_snapshots WHERE mastra_run_id = ?
      `,
      args: [mastraRunId],
    });
    const row = result.rows[0];
    if (!row) return null;
    return {
      mastraRunId: String(row.mastra_run_id),
      pythonRunId: String(row.python_run_id),
      workflowId: String(row.workflow_id),
    };
  }

  async getConfirmation(mastraRunId: string): Promise<Confirmation | null> {
    const result = await this.client.execute({
      sql: "SELECT confirmation_json FROM m0_runs WHERE mastra_run_id = ?",
      args: [mastraRunId],
    });
    const row = result.rows[0];
    if (!row) return null;
    if (row.confirmation_json === null) return null;
    try {
      return confirmationSchema.parse(JSON.parse(String(row.confirmation_json)));
    } catch {
      throw new Error("confirmation storage corruption");
    }
  }

  async putConfirmation(
    mastraRunId: string,
    receipt: Confirmation,
  ): Promise<"stored" | "same" | "conflict"> {
    const normalized = confirmationSchema.parse(receipt);
    const serialized = JSON.stringify(normalized);
    const updated = await this.client.execute({
      sql: `
        UPDATE m0_runs SET confirmation_json = ?
        WHERE mastra_run_id = ? AND confirmation_json IS NULL
      `,
      args: [serialized, mastraRunId],
    });
    if (updated.rowsAffected === 1) return "stored";
    const current = await this.getConfirmation(mastraRunId);
    if (current === null) throw new Error("correlation is missing");
    return JSON.stringify(current) === serialized ? "same" : "conflict";
  }

  async claimContinue(
    mastraRunId: string,
    owner = "legacy-owner",
    nowMs = Date.now(),
    leaseMs = 30_000,
  ): Promise<boolean> {
    if (!owner.trim() || !Number.isSafeInteger(nowMs) || leaseMs <= 0) {
      throw new Error("invalid continue lease")
    }
    const result = await this.client.execute({
      sql: `
        UPDATE m0_runs
        SET continue_state = 'claimed', continue_owner = ?,
            continue_lease_until_ms = ?
        WHERE mastra_run_id = ?
          AND confirmation_json IS NOT NULL
          AND (
            continue_state = 'idle'
            OR (
              continue_state = 'claimed'
              AND (
                continue_lease_until_ms IS NULL
                OR continue_lease_until_ms <= ?
              )
            )
          )
      `,
      args: [owner, nowMs + leaseMs, mastraRunId, nowMs],
    });
    return result.rowsAffected === 1;
  }

  async releaseContinue(
    mastraRunId: string,
    owner = "legacy-owner",
  ): Promise<boolean> {
    const result = await this.client.execute({
      sql: `
        UPDATE m0_runs
        SET continue_state = 'idle', continue_owner = NULL,
            continue_lease_until_ms = NULL
        WHERE mastra_run_id = ? AND continue_state = 'claimed'
          AND continue_owner = ?
      `,
      args: [mastraRunId, owner],
    });
    return result.rowsAffected === 1;
  }

  async completeContinue(
    mastraRunId: string,
    owner = "legacy-owner",
  ): Promise<boolean> {
    const result = await this.client.execute({
      sql: `
        UPDATE m0_runs
        SET continue_state = 'complete', continue_owner = NULL,
            continue_lease_until_ms = NULL
        WHERE mastra_run_id = ? AND continue_state = 'claimed'
          AND continue_owner = ?
      `,
      args: [mastraRunId, owner],
    });
    return result.rowsAffected === 1;
  }

  async close(): Promise<void> {
    this.client.close();
  }
}
