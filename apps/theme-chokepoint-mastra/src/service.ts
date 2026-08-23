import { randomUUID } from "node:crypto";

import { z } from "zod";

import { failure, type Failure } from "./errors.js";
import {
  decodeRunResponse,
  decodeFailureResponse,
  confirmEnvelopeSchema,
  KNOWN_PYTHON_STATUSES,
  manifestEnvelopeSchema,
  manifestIsSemanticallyValid,
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
    claimContinue?(
      mastraRunId: string,
      owner?: string,
      nowMs?: number,
      leaseMs?: number,
    ): Promise<boolean>;
    releaseContinue?(
      mastraRunId: string,
      owner?: string,
    ): Promise<boolean | void>;
    completeContinue?(
      mastraRunId: string,
      owner?: string,
    ): Promise<boolean | void>;
  };
  workflow: {
    getSnapshot(mastraRunId: string): Promise<Correlation | null>;
    createSnapshot?(correlation: Correlation): Promise<void>;
    start?(correlation: Correlation): Promise<unknown>;
    resume?(
      correlation: Correlation,
      confirmation: Confirmation,
    ): Promise<unknown>;
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
      !this.ports.workflow.start &&
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
        if (this.ports.workflow.start) {
          const started = await this.ports.workflow.start(correlation);
          if (!hasWorkflowStatus(started, "suspended")) {
            return failure("PYTHON_STATUS_MISMATCH");
          }
        } else {
          await this.ports.workflow.createSnapshot!(correlation);
        }
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
    if (!parsed.success) {
      return failure("INVALID_ARGUMENT");
    }
    if (
      new Set(parsed.data.selectedAnchorIds).size !==
      parsed.data.selectedAnchorIds.length
    ) {
      return failure("DUPLICATE_ANCHOR_ID");
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
      const pendingFailure = decodeFailureResponse(pendingRaw);
      if (pendingFailure) return pendingFailure;
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
        return failure("ANCHOR_NOT_PROPOSED_FOR_RUN");
      }
      const confirmedRaw = await this.ports!.python.call(
        "theme_chokepoint_confirm_anchors",
        {
          run_id: correlation.pythonRunId,
          anchor_ids: parsed.data.selectedAnchorIds,
          confirmed_by: parsed.data.actor,
        },
      );
      const confirmationFailure = decodeFailureResponse(confirmedRaw);
      if (confirmationFailure) return confirmationFailure;
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
      const receipt: Confirmation = {
        runId: confirmed.data.data.run_id,
        confirmedAnchorIds: [...confirmed.data.data.confirmed_anchor_ids],
        confirmedBy: confirmed.data.data.confirmed_by,
        confirmedAt: confirmed.data.data.confirmed_at,
      };
      const stored = await this.persistConfirmation(correlation, receipt);
      if (stored) return stored;
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

  async getM0PendingAnchors(_input: unknown): Promise<unknown> {
    const parsed = querySchema.safeParse(_input);
    if (!parsed.success) {
      return failure("INVALID_ARGUMENT");
    }
    try {
      const correlation = await this.correlatedRun(parsed.data.mastraRunId);
      if (isFailure(correlation)) return correlation;
      const current = await this.readPythonRun(correlation);
      if (isFailure(current)) return current;
      if (current.status !== "AWAITING_PRODUCT_CONFIRMATION") {
        return failure("RUN_NOT_AWAITING_CONFIRMATION");
      }
      const raw = await this.ports!.python.call(
        "theme_chokepoint_get_pending_anchors",
        { run_id: correlation.pythonRunId },
      );
      const pythonFailure = decodeFailureResponse(raw);
      if (pythonFailure) return pythonFailure;
      const pending = pendingEnvelopeSchema.safeParse(raw);
      if (
        !pending.success ||
        pending.data.data.run_id !== correlation.pythonRunId
      ) {
        return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
      }
      return {
        ok: true,
        data: {
          mastraRunId: correlation.mastraRunId,
          pythonRunId: correlation.pythonRunId,
          status: pending.data.data.status,
          anchors: pending.data.data.anchors,
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
      const pythonFailure = decodeFailureResponse(raw);
      if (pythonFailure) return pythonFailure;
      const manifest = manifestEnvelopeSchema.safeParse(raw);
      if (
        !manifest.success ||
        !manifestIsSemanticallyValid(
          manifest.data.data,
          correlation.pythonRunId,
          current.status,
        )
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
    const ownerToken = randomUUID();
    let owner: boolean;
    try {
      owner = await store.claimContinue(
        correlation.mastraRunId,
        ownerToken,
        Date.now(),
        30_000,
      );
    } catch (error) {
      return storageFailure(error);
    }
    if (!owner) {
      const reconciled = await this.readPythonRun(correlation);
      if (isFailure(reconciled)) return reconciled;
      return reconciled.status === "READY_FOR_SUPPLY_CHAIN"
        ? failure("CONCURRENT_OR_DUPLICATE_RESUME")
        : this.readArtifacts(correlation, reconciled);
    }
    if (this.ports!.workflow.resume) {
      return this.continueThroughWorkflow(
        correlation,
        receipt,
        ownerToken,
      );
    }
    try {
      const raw = await this.ports!.python.call("theme_chokepoint_continue", {
        run_id: correlation.pythonRunId,
      });
      const pythonFailure = decodeFailureResponse(raw);
      if (pythonFailure) {
        await store.releaseContinue?.(correlation.mastraRunId, ownerToken);
        return pythonFailure;
      }
      const manifest = manifestEnvelopeSchema.safeParse(raw);
      if (
        !manifest.success ||
        !manifestIsSemanticallyValid(
          manifest.data.data,
          correlation.pythonRunId,
        )
      ) {
        try {
          await store.releaseContinue?.(correlation.mastraRunId, ownerToken);
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
        await store.completeContinue(correlation.mastraRunId, ownerToken);
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
        await store.releaseContinue?.(correlation.mastraRunId, ownerToken);
      } catch (storageError) {
        return storageFailure(storageError);
      }
      return this.mapException(error);
    }
  }

  private async continueThroughWorkflow(
    correlation: Correlation,
    receipt: Confirmation,
    ownerToken: string,
  ): Promise<unknown> {
    const store = this.ports!.store;
    try {
      const result = await this.ports!.workflow.resume!(correlation, receipt);
      const suspendedFailure = decodeWorkflowSuspension(result);
      if (suspendedFailure) {
        await store.releaseContinue?.(correlation.mastraRunId, ownerToken);
        return suspendedFailure;
      }
      if (!hasWorkflowStatus(result, "success")) {
        await store.releaseContinue?.(correlation.mastraRunId, ownerToken);
        return failure("PYTHON_STATUS_MISMATCH");
      }
      const current = await this.readPythonRun(correlation);
      if (isFailure(current)) {
        await store.releaseContinue?.(correlation.mastraRunId, ownerToken);
        return current;
      }
      if (current.status === "READY_FOR_SUPPLY_CHAIN") {
        await store.releaseContinue?.(correlation.mastraRunId, ownerToken);
        return failure("PYTHON_STATUS_MISMATCH");
      }
      const artifacts = await this.readArtifacts(correlation, current);
      if (isFailureResponse(artifacts)) {
        await store.releaseContinue?.(correlation.mastraRunId, ownerToken);
        return artifacts;
      }
      const completed = await store.completeContinue!(
        correlation.mastraRunId,
        ownerToken,
      );
      if (completed === false) return failure("STORAGE_FAILURE");
      return artifacts;
    } catch (error) {
      try {
        await store.releaseContinue?.(correlation.mastraRunId, ownerToken);
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
    const pythonFailure = decodeFailureResponse(raw);
    if (pythonFailure) return pythonFailure;
    const manifest = manifestEnvelopeSchema.safeParse(raw);
    if (
      !manifest.success ||
      !manifestIsSemanticallyValid(
        manifest.data.data,
        correlation.pythonRunId,
        current.status,
      )
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

function isFailureResponse(value: unknown): value is Failure {
  return (
    typeof value === "object" &&
    value !== null &&
    "ok" in value &&
    value.ok === false
  );
}

function hasWorkflowStatus(value: unknown, status: string): boolean {
  return (
    typeof value === "object" &&
    value !== null &&
    "status" in value &&
    value.status === status
  );
}

function decodeWorkflowSuspension(value: unknown): Failure | null {
  if (
    typeof value !== "object" ||
    value === null ||
    !("status" in value) ||
    value.status !== "suspended" ||
    !("suspendPayload" in value) ||
    typeof value.suspendPayload !== "object" ||
    value.suspendPayload === null
  ) {
    return null;
  }
  const payload = (value.suspendPayload as Record<string, unknown>)[
    "continue-python-run"
  ];
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("error" in payload)
  ) {
    return failure("PYTHON_STATUS_MISMATCH");
  }
  return decodeFailureResponse({ ok: false, error: payload.error }) ??
    failure("MCP_RESPONSE_SCHEMA_MISMATCH");
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
