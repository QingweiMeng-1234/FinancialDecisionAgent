import { randomUUID } from "node:crypto";

import { MCPClient } from "@mastra/mcp";

type Diagnostic = {
  tool: string;
  code: "MCP_TOOL_FAILURE";
  retryable: boolean;
};

type Options = {
  url: string;
  timeoutMs?: number;
  logger?: (event: Diagnostic) => void;
};

export class M0McpError extends Error {
  readonly code = "MCP_TOOL_FAILURE" as const;

  constructor(readonly retryable: boolean) {
    super("MCP operation failed");
    this.name = "M0McpError";
  }

  toJSON() {
    return {
      code: this.code,
      message: this.message,
      retryable: this.retryable,
    };
  }
}

export class MastraMcpPythonClient {
  private readonly client: MCPClient;
  private readonly logger: (event: Diagnostic) => void;
  private readonly timeoutMs: number;

  constructor(options: Options) {
    const endpoint = validateEndpoint(options.url);
    this.timeoutMs = options.timeoutMs ?? 5_000;
    this.logger = options.logger ?? (() => undefined);
    this.client = new MCPClient({
      id: `theme-chokepoint-${randomUUID()}`,
      timeout: this.timeoutMs,
      servers: {
        python: {
          url: endpoint,
          allowedHosts: [endpoint.host],
          connectTimeout: this.timeoutMs,
        },
      },
    });
    (this.client as unknown as { __setLogger(logger: unknown): void }).__setLogger(
      {
        debug() {},
        info() {},
        warn() {},
        error() {},
        trackException() {},
      },
    );
  }

  async call(toolName: string, input: Record<string, unknown>): Promise<unknown> {
    try {
      const discovered = await this.client.listToolsetsWithErrors({
        perServerTimeoutMs: this.timeoutMs,
      });
      if (discovered.errors.python) {
        throw new M0McpError(true);
      }
      const tool = discovered.toolsets.python?.[toolName] as
        | {
            execute?: (
              input: Record<string, unknown>,
              options: Record<string, unknown>,
            ) => Promise<unknown>;
          }
        | undefined;
      if (!tool?.execute) throw new M0McpError(false);
      const raw = await tool.execute(input, {
        toolCallId: `m0-${toolName}`,
        messages: [],
      });
      return decodeToolResult(raw);
    } catch (error) {
      const normalized =
        error instanceof M0McpError
          ? error
          : new M0McpError(isTransient(error));
      this.logger({
        tool: toolName,
        code: normalized.code,
        retryable: normalized.retryable,
      });
      throw normalized;
    }
  }

  async close(): Promise<void> {
    try {
      await this.client.disconnect();
    } catch {
      // Teardown diagnostics are dependency-private and never leave this adapter.
    }
  }
}

function validateEndpoint(value: string): URL {
  const endpoint = new URL(value);
  if (
    endpoint.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "[::1]"].includes(endpoint.hostname) ||
    endpoint.pathname !== "/mcp" ||
    endpoint.username ||
    endpoint.password ||
    endpoint.search ||
    endpoint.hash
  ) {
    throw new M0McpError(false);
  }
  return endpoint;
}

function decodeToolResult(value: unknown): unknown {
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      throw new M0McpError(false);
    }
  }
  if (
    typeof value === "object" &&
    value !== null &&
    "ok" in value &&
    typeof value.ok === "boolean"
  ) {
    return value;
  }
  if (
    typeof value === "object" &&
    value !== null &&
    Object.keys(value).length === 1 &&
    "result" in value
  ) {
    return decodeToolResult(value.result);
  }
  if (
    typeof value !== "object" ||
    value === null ||
    !("content" in value) ||
    !Array.isArray(value.content) ||
    value.content.length !== 1
  ) {
    throw new M0McpError(false);
  }
  const block = value.content[0];
  if (
    typeof block !== "object" ||
    block === null ||
    !("type" in block) ||
    block.type !== "text" ||
    !("text" in block) ||
    typeof block.text !== "string"
  ) {
    throw new M0McpError(false);
  }
  try {
    return JSON.parse(block.text);
  } catch {
    throw new M0McpError(false);
  }
}

function isTransient(error: unknown): boolean {
  const code =
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string"
      ? error.code
      : "";
  return new Set(["ECONNREFUSED", "ECONNRESET", "ETIMEDOUT", "EPIPE"]).has(code);
}
