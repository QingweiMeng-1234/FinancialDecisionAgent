import { z } from "zod";

import { failure, type Failure } from "./errors.js";
import {
  decodeRunResponse,
  decodeFailureResponse,
  confirmEnvelopeSchema,
  KNOWN_PYTHON_STATUSES,
  manifestEnvelopeSchema,
  pendingEnvelopeSchema,
  type DecodedRun,
  type Confirmation,
} from "./schemas.js";

type Correlation = {
  mastraRunId: string;
  pythonRunId: string;
  workflowId: string;
};

type ServicePorts = {
  store: {
    getCorrelation(mastraRunId: string): Promise<Correlation | null>;
    createCorrelation?(correlation: Correlation): Promise<void>;
    getConfirmation?(mastraRunId: string): Promise<Confirmation | null>;
    putConfirmation?(
      mastraRunId: string,
      receipt: Confirmation,
    ): Promise<"stored" | "same" | "conflict">;
    claimContinue?(mastraRunId: string): Promise<boolean>;
    releaseContinue?(mastraRunId: string): Promise<void>;
    completeContinue?(mastraRunId: string): Promise<void>;
  };
  workflow: {
    getSnapshot(mastraRunId: string): Promise<Correlation | null>;
    createSnapshot?(correlation: Correlation): Promise<void>;
  };
  python: {
    call(tool: string, input: Record<string, unknown>): Promise<unknown>;
  };
};

const runId = z.string().trim().min(1);
const startSchema = z
  .object({
    mastraRunId: runId,
    pythonRequest: z.record(z.string(), z.unknown()),
  })
  .strict();
const resumeSchema = z
  .object({
    mastraRunId: runId,
    actor: z.string().trim().min(1),
    selectedAnchorIds: z.array(z.string().trim().min(1)).min(1),
  })
  .strict();
const querySchema = z.object({ mastraRunId: runId }).strict();

export class M0Service {
  constructor(private readonly ports?: ServicePorts) {}

  async startM0(_input: unknown): Promise<unknown> {
    const parsed = startSchema.safeParse(_input);
    if (
      !parsed.success ||
      typeof parsed.data.pythonRequest.run_id !== "string" ||
      !parsed.data.pythonRequest.run_id.trim()
    ) {
      return failure("INVALID_ARGUMENT");
    }
    if (
      !this.ports?.store.createCorrelation ||
      !this.ports.workflow.createSnapshot
    ) {
      return failure("RUNTIME_NOT_CONFIGURED");
    }
    const pythonRunId = parsed.data.pythonRequest.run_id.trim();
    try {
      const raw = await this.ports.python.call(
        "theme_chokepoint_start",
        parsed.data.pythonRequest,
      );
      const pythonFailure = decodeFailureResponse(raw);
      if (pythonFailure) return pythonFailure;
      const decoded = decodeRunResponse(raw, pythonRunId);
      if (decoded.kind === "unknown-status") {
        return failure("UNKNOWN_PYTHON_STATUS");
      }
      if (decoded.kind === "schema-mismatch") {
        return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
      }
      const correlation = {
        mastraRunId: parsed.data.mastraRunId,
        pythonRunId,
        workflowId: "theme-chokepoint-m0",
      };
      try {
        await this.ports.workflow.createSnapshot(correlation);
        await this.ports.store.createCorrelation(correlation);
      } catch (error) {
        return storageFailure(error);
      }
      return {
        ok: true,
        data: {
          mastraRunId: correlation.mastraRunId,
          pythonRunId,
          status: decoded.data.status,
          nextStage: decoded.data.nextStage,
          confirmation: decoded.data.confirmation,
        },
      };
    } catch (error) {
      return this.mapException(error);
    }
  }

  async resumeM0(_input: unknown): Promise<unknown> {
    const parsed = resumeSchema.safeParse(_input);
    if (
      !parsed.success ||
      new Set(parsed.data.selectedAnchorIds).size !==
        parsed.data.selectedAnchorIds.length
    ) {
      return failure("INVALID_ARGUMENT");
    }
    try {
      const correlation = await this.correlatedRun(parsed.data.mastraRunId);
      if (isFailure(correlation)) return correlation;
      const current = await this.readPythonRun(correlation);
      if (isFailure(current)) return current;
      if (current.confirmation !== null) {
        if (
          !confirmationMatchesRequest(
            current.confirmation,
            parsed.data.actor,
            parsed.data.selectedAnchorIds,
          )
        ) {
          return failure("CONCURRENT_OR_DUPLICATE_RESUME");
        }
        const stored = await this.persistConfirmation(
          correlation,
          current.confirmation,
        );
        if (stored) return stored;
        if (current.status === "READY_FOR_SUPPLY_CHAIN") {
          return this.continueFromReady(correlation, current.confirmation);
        }
        return this.readArtifacts(correlation, current);
      }
      if (
        current.status !== "AWAITING_PRODUCT_CONFIRMATION" ||
        current.confirmation !== null
      ) {
        return failure("RUNTIME_NOT_CONFIGURED");
      }
      const pendingRaw = await this.ports!.python.call(
        "theme_chokepoint_get_pending_anchors",
        { run_id: correlation.pythonRunId },
      );
      const pending = pendingEnvelopeSchema.safeParse(pendingRaw);
      if (
        !pending.success ||
        pending.data.data.run_id !== correlation.pythonRunId
      ) {
        return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
      }
      const proposed = new Set(
        pending.data.data.anchors.map((anchor) => anchor.anchor_id),
      );
      if (
        parsed.data.selectedAnchorIds.some((anchorId) => !proposed.has(anchorId))
      ) {
        return failure("INVALID_ARGUMENT");
      }
      const confirmedRaw = await this.ports!.python.call(
        "theme_chokepoint_confirm_anchors",
        {
          run_id: correlation.pythonRunId,
          anchor_ids: parsed.data.selectedAnchorIds,
          confirmed_by: parsed.data.actor,
        },
      );
      const confirmed = confirmEnvelopeSchema.safeParse(confirmedRaw);
      if (
        !confirmed.success ||
        !confirmMatches(
          confirmed.data.data,
          correlation.pythonRunId,
          parsed.data.actor,
          parsed.data.selectedAnchorIds,
        )
      ) {
        return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
      }
      if (!this.ports!.store.putConfirmation) {
        return failure("RUNTIME_NOT_CONFIGURED");
      }
      const receipt: Confirmation = {
        runId: confirmed.data.data.run_id,
        confirmedAnchorIds: [...confirmed.data.data.confirmed_anchor_ids],
        confirmedBy: confirmed.data.data.confirmed_by,
        confirmedAt: confirmed.data.data.confirmed_at,
      };
      const stored = await this.ports!.store.putConfirmation(
        correlation.mastraRunId,
        receipt,
      );
      if (stored === "conflict") {
        return failure("CONCURRENT_OR_DUPLICATE_RESUME");
      }
      return this.continueFromReady(correlation, receipt);
    } catch (error) {
      return this.mapException(error);
    }
  }

  async getM0Status(_input: unknown): Promise<unknown> {
    const parsed = querySchema.safeParse(_input);
    if (!parsed.success) {
      return failure("INVALID_ARGUMENT");
    }
    try {
      const correlation = await this.correlatedRun(parsed.data.mastraRunId);
      if (isFailure(correlation)) return correlation;
      const current = await this.readPythonRun(correlation);
      if (isFailure(current)) return current;
      return {
        ok: true,
        data: {
          mastraRunId: correlation.mastraRunId,
          pythonRunId: correlation.pythonRunId,
          status: current.status,
          nextStage: current.nextStage,
          confirmation: current.confirmation,
          recoverable: current.status === "READY_FOR_SUPPLY_CHAIN",
        },
      };
    } catch (error) {
      return this.mapException(error);
    }
  }

  async getM0Artifacts(_input: unknown): Promise<unknown> {
    const parsed = querySchema.safeParse(_input);
    if (!parsed.success) {
      return failure("INVALID_ARGUMENT");
    }
    try {
      const correlation = await this.correlatedRun(parsed.data.mastraRunId);
      if (isFailure(correlation)) return correlation;
      const current = await this.readPythonRun(correlation);
      if (isFailure(current)) return current;
      const raw = await this.ports!.python.call(
        "theme_chokepoint_get_artifacts",
        { run_id: correlation.pythonRunId },
      );
      const manifest = manifestEnvelopeSchema.safeParse(raw);
      if (
        !manifest.success ||
        manifest.data.data.run_id !== correlation.pythonRunId
      ) {
        return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
      }
      return {
        ok: true,
        data: {
          mastraRunId: correlation.mastraRunId,
          pythonRunId: correlation.pythonRunId,
          status: current.status,
          confirmation: current.confirmation,
          manifest: manifest.data.data,
        },
      };
    } catch (error) {
      return this.mapException(error);
    }
  }

  private async correlatedRun(mastraRunId: string) {
    if (!this.ports) return failure("RUNTIME_NOT_CONFIGURED");
    let correlation: Correlation | null;
    let snapshot: Correlation | null;
    try {
      [correlation, snapshot] = await Promise.all([
        this.ports.store.getCorrelation(mastraRunId),
        this.ports.workflow.getSnapshot(mastraRunId),
      ]);
    } catch (error) {
      return storageFailure(error);
    }
    if (!correlation || !snapshot) {
      return failure("MASTRA_RUN_NOT_FOUND");
    }
    if (
      correlation.mastraRunId !== mastraRunId ||
      snapshot.mastraRunId !== mastraRunId ||
      correlation.workflowId !== "theme-chokepoint-m0" ||
      snapshot.workflowId !== "theme-chokepoint-m0" ||
      correlation.pythonRunId !== snapshot.pythonRunId
    ) {
      return failure("RUN_CORRELATION_MISMATCH");
    }
    return correlation;
  }

  private async readPythonRun(
    correlation: Correlation,
  ): Promise<DecodedRun | Failure> {
    const raw = await this.ports!.python.call("theme_chokepoint_get_run", {
      run_id: correlation.pythonRunId,
    });
    const pythonFailure = decodeFailureResponse(raw);
    if (pythonFailure) return pythonFailure;
    const decoded = decodeRunResponse(raw, correlation.pythonRunId);
    if (decoded.kind === "unknown-status") {
      return failure("UNKNOWN_PYTHON_STATUS");
    }
    if (decoded.kind === "schema-mismatch") {
      return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
    }
    return decoded.data;
  }

  private async persistConfirmation(
    correlation: Correlation,
    receipt: Confirmation,
  ): Promise<Failure | null> {
    if (!this.ports!.store.putConfirmation) {
      return failure("RUNTIME_NOT_CONFIGURED");
    }
    try {
      const result = await this.ports!.store.putConfirmation(
        correlation.mastraRunId,
        receipt,
      );
      return result === "conflict"
        ? failure("CONCURRENT_OR_DUPLICATE_RESUME")
        : null;
    } catch (error) {
      return storageFailure(error);
    }
  }

  private async continueFromReady(
    correlation: Correlation,
    receipt: Confirmation,
  ): Promise<unknown> {
    const store = this.ports!.store;
    if (!store.claimContinue || !store.completeContinue) {
      return failure("RUNTIME_NOT_CONFIGURED");
    }
    let owner: boolean;
    try {
      owner = await store.claimContinue(correlation.mastraRunId);
    } catch (error) {
      return storageFailure(error);
    }
    if (!owner) return failure("CONCURRENT_OR_DUPLICATE_RESUME");
    try {
      const raw = await this.ports!.python.call("theme_chokepoint_continue", {
        run_id: correlation.pythonRunId,
      });
      const manifest = manifestEnvelopeSchema.safeParse(raw);
      if (
        !manifest.success ||
        manifest.data.data.run_id !== correlation.pythonRunId
      ) {
        try {
          await store.releaseContinue?.(correlation.mastraRunId);
        } catch (error) {
          return storageFailure(error);
        }
        return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
      }
      if (!KNOWN_PYTHON_STATUSES.includes(
        manifest.data.data.final_status as (typeof KNOWN_PYTHON_STATUSES)[number],
      )) {
        try {
          await store.releaseContinue?.(correlation.mastraRunId);
        } catch (error) {
          return storageFailure(error);
        }
        return failure("UNKNOWN_PYTHON_STATUS");
      }
      try {
        await store.completeContinue(correlation.mastraRunId);
      } catch (error) {
        return storageFailure(error);
      }
      return {
        ok: true,
        data: {
          mastraRunId: correlation.mastraRunId,
          pythonRunId: correlation.pythonRunId,
          status: manifest.data.data.final_status,
          confirmation: receipt,
          manifest: manifest.data.data,
        },
      };
    } catch (error) {
      try {
        await store.releaseContinue?.(correlation.mastraRunId);
      } catch (storageError) {
        return storageFailure(storageError);
      }
      return this.mapException(error);
    }
  }

  private async readArtifacts(
    correlation: Correlation,
    current: DecodedRun,
  ): Promise<unknown> {
    const raw = await this.ports!.python.call(
      "theme_chokepoint_get_artifacts",
      { run_id: correlation.pythonRunId },
    );
    const manifest = manifestEnvelopeSchema.safeParse(raw);
    if (
      !manifest.success ||
      manifest.data.data.run_id !== correlation.pythonRunId ||
      manifest.data.data.final_status !== current.status
    ) {
      return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
    }
    return {
      ok: true,
      data: {
        mastraRunId: correlation.mastraRunId,
        pythonRunId: correlation.pythonRunId,
        status: current.status,
        confirmation: current.confirmation,
        manifest: manifest.data.data,
      },
    };
  }

  private mapException(error: unknown): Failure {
    const code =
      typeof error === "object" &&
      error !== null &&
      "code" in error &&
      typeof error.code === "string"
        ? error.code
        : "";
    return failure(
      "MCP_TOOL_FAILURE",
      new Set([
        "ECONNREFUSED",
        "ECONNRESET",
        "ETIMEDOUT",
        "EPIPE",
        "MCP_TOOL_FAILURE",
      ]).has(code) &&
        !(
          typeof error === "object" &&
          error !== null &&
          "retryable" in error &&
          error.retryable === false
        ),
    );
  }
}

function isFailure(value: Correlation | DecodedRun | Failure): value is Failure {
  return "ok" in value && value.ok === false;
}

function confirmMatches(
  response: {
    run_id: string;
    confirmed_by: string;
    confirmed_anchor_ids: string[];
    confirmed_at: string;
  },
  runId: string,
  actor: string,
  selectedIds: string[],
) {
  const responseIds = response.confirmed_anchor_ids;
  return (
    response.run_id === runId &&
    response.confirmed_by === actor &&
    response.confirmed_by.trim() === response.confirmed_by &&
    responseIds.length === new Set(responseIds).size &&
    responseIds.length === selectedIds.length &&
    responseIds.every((value) => selectedIds.includes(value)) &&
    /(?:Z|[+-]\d\d:\d\d)$/.test(response.confirmed_at) &&
    Number.isFinite(Date.parse(response.confirmed_at))
  );
}

function confirmationMatchesRequest(
  receipt: Confirmation,
  actor: string,
  selectedIds: string[],
) {
  return (
    receipt.confirmedBy === actor &&
    receipt.confirmedAnchorIds.length ===
      new Set(receipt.confirmedAnchorIds).size &&
    receipt.confirmedAnchorIds.length === selectedIds.length &&
    receipt.confirmedAnchorIds.every((value) => selectedIds.includes(value))
  );
}

function storageFailure(error: unknown): Failure {
  const code =
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string"
      ? error.code
      : "";
  return failure(
    "STORAGE_FAILURE",
    new Set([
      "SQLITE_BUSY",
      "SQLITE_LOCKED",
      "LIBSQL_CLIENT_CLOSED",
      "LIBSQL_SERVER_UNAVAILABLE",
    ]).has(code),
  );
}
