export const ERROR_MESSAGES = {
  INVALID_ARGUMENT: "Invalid request",
  RUNTIME_NOT_CONFIGURED: "Runtime is not configured",
  MCP_TOOL_FAILURE: "MCP operation failed",
  STORAGE_FAILURE: "Persistent storage operation failed",
  MASTRA_RUN_NOT_FOUND: "Mastra run was not found",
  RUN_NOT_FOUND: "Run was not found",
  RUN_CORRELATION_MISMATCH: "Run correlation mismatch",
  MCP_RESPONSE_SCHEMA_MISMATCH: "MCP response schema mismatch",
  CONCURRENT_OR_DUPLICATE_RESUME: "Confirmation or resume conflicts with durable state",
  UNKNOWN_PYTHON_STATUS: "Python returned an unknown run status",
  PYTHON_STATUS_MISMATCH: "Python run status is inconsistent",
  RUN_NOT_AWAITING_CONFIRMATION: "Run is not awaiting confirmation",
  ANCHOR_NOT_PROPOSED_FOR_RUN: "Anchor is not proposed for this run",
  DUPLICATE_ANCHOR_ID: "Anchor IDs must be unique",
  RUN_ALREADY_EXISTS: "Run already exists",
} as const;

export type StableErrorCode = keyof typeof ERROR_MESSAGES;

export type Failure = {
  ok: false;
  error: {
    code: StableErrorCode;
    message: string;
    retryable: boolean;
  };
};

export function failure(
  code: StableErrorCode,
  retryable = false,
): Failure {
  return {
    ok: false,
    error: {
      code,
      message: ERROR_MESSAGES[code],
      retryable,
    },
  };
}
