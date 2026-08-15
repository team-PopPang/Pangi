import type { components, operations } from "./generated";

export type BootstrapAdminRequest =
  operations["createBootstrapAdmin"]["requestBody"]["content"]["application/json"];
export type BootstrapAdminResponse =
  operations["createBootstrapAdmin"]["responses"][201]["content"]["application/json"];
export type LoginRequest = operations["login"]["requestBody"]["content"]["application/json"];
export type SessionEnvelope =
  operations["getAuthSession"]["responses"][200]["content"]["application/json"];
export type SessionInfo = SessionEnvelope["session"];
export type RunListQuery = NonNullable<operations["listRuns"]["parameters"]["query"]>;
export type RunListEnvelope =
  operations["listRuns"]["responses"][200]["content"]["application/json"];
export type RunEnvelope =
  operations["getRun"]["responses"][200]["content"]["application/json"];
export type RunCancellationEnvelope =
  operations["cancelRun"]["responses"][200]["content"]["application/json"];
export type RunEventListEnvelope =
  operations["getRunEvents"]["responses"][200]["content"]["application/json"];
export type RunEvent = components["schemas"]["RunEventResponse"];
export type RunQueueMetrics =
  operations["getRunQueueMetrics"]["responses"][200]["content"]["application/json"];

type ErrorEnvelope = components["schemas"]["ErrorEnvelope"];

type RequestOptions = {
  method: "GET" | "POST";
  body?: object;
  csrf?: boolean;
};

type ApiErrorOptions = {
  status: number;
  code: string;
  message: string;
  requestId: string | null;
  retryAfterSeconds: number | null;
  cause?: unknown;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(options: ApiErrorOptions) {
    super(options.message, { cause: options.cause });
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.requestId = options.requestId;
    this.retryAfterSeconds = options.retryAfterSeconds;
  }
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const item of document.cookie.split(";")) {
    const cookie = item.trim();
    if (cookie.startsWith(prefix)) {
      return decodeURIComponent(cookie.slice(prefix.length));
    }
  }
  return null;
}

function csrfToken(): string | null {
  return readCookie("__Host-pangi_csrf") ?? readCookie("pangi_csrf");
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error = value.error;
  return (
    typeof error === "object"
    && error !== null
    && "code" in error
    && typeof error.code === "string"
    && "message" in error
    && typeof error.message === "string"
    && "request_id" in error
    && typeof error.request_id === "string"
  );
}

function retryAfterSeconds(response: Response): number | null {
  const value = response.headers.get("Retry-After");
  if (value === null) {
    return null;
  }
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.ceil(seconds);
  }
  const retryAt = Date.parse(value);
  if (Number.isNaN(retryAt)) {
    return null;
  }
  return Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
}

function runPath(runId: string, suffix = ""): string {
  return `/api/v1/runs/${encodeURIComponent(runId)}${suffix}`;
}

function runListPath(query: RunListQuery): string {
  const search = new URLSearchParams();
  if (query.cursor !== undefined && query.cursor !== null) {
    search.set("cursor", query.cursor);
  }
  if (query.limit !== undefined) {
    search.set("limit", String(query.limit));
  }
  for (const state of query.state ?? []) {
    search.append("state", state);
  }
  for (const trigger of query.trigger ?? []) {
    search.append("trigger", trigger);
  }
  const serialized = search.toString();
  return serialized === "" ? "/api/v1/runs" : `/api/v1/runs?${serialized}`;
}

async function request<T>(path: string, options: RequestOptions): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (options.csrf === true) {
    const token = csrfToken();
    if (token === null) {
      throw new ApiError({
        status: 0,
        code: "csrf_token_unavailable",
        message: "The browser session is missing its CSRF token",
        requestId: null,
        retryAfterSeconds: null,
      });
    }
    headers.set("X-CSRF-Token", token);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method,
      credentials: "same-origin",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (cause) {
    throw new ApiError({
      status: 0,
      code: "network_error",
      message: "The API request could not be completed",
      requestId: null,
      retryAfterSeconds: null,
      cause,
    });
  }

  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    const envelope = isErrorEnvelope(payload) ? payload : null;
    throw new ApiError({
      status: response.status,
      code: envelope?.error.code ?? "request_failed",
      message: envelope?.error.message ?? "The API request failed",
      requestId: envelope?.error.request_id ?? response.headers.get("X-Request-ID"),
      retryAfterSeconds: retryAfterSeconds(response),
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return await response.json() as T;
}

export const adminApi = {
  createBootstrapAdmin(payload: BootstrapAdminRequest): Promise<BootstrapAdminResponse> {
    return request("/api/v1/bootstrap/admin", { method: "POST", body: payload });
  },

  login(payload: LoginRequest): Promise<SessionEnvelope> {
    return request("/api/v1/auth/login", { method: "POST", body: payload });
  },

  getSession(): Promise<SessionEnvelope> {
    return request("/api/v1/auth/session", { method: "GET" });
  },

  rotateSession(): Promise<SessionEnvelope> {
    return request("/api/v1/auth/session/rotate", { method: "POST", csrf: true });
  },

  logout(): Promise<void> {
    return request("/api/v1/auth/logout", { method: "POST", csrf: true });
  },

  listRuns(query: RunListQuery = {}): Promise<RunListEnvelope> {
    return request(runListPath(query), { method: "GET" });
  },

  getRun(runId: string): Promise<RunEnvelope> {
    return request(runPath(runId), { method: "GET" });
  },

  cancelRun(runId: string): Promise<RunCancellationEnvelope> {
    return request(runPath(runId, "/cancel"), { method: "POST", csrf: true });
  },

  getRunEvents(
    runId: string,
    options: { after?: number; limit?: number } = {},
  ): Promise<RunEventListEnvelope> {
    const search = new URLSearchParams();
    if (options.after !== undefined) {
      search.set("after", String(options.after));
    }
    if (options.limit !== undefined) {
      search.set("limit", String(options.limit));
    }
    const serialized = search.toString();
    const path = runPath(runId, "/events");
    return request(serialized === "" ? path : `${path}?${serialized}`, { method: "GET" });
  },

  getRunQueueMetrics(): Promise<RunQueueMetrics> {
    return request("/api/v1/runs/metrics", { method: "GET" });
  },
};

export type RunEventStreamCallbacks = {
  onEvent: (event: RunEvent) => void;
  onError?: (event: Event) => void;
  onProtocolError?: (error: unknown) => void;
};

export function openRunEventStream(
  runId: string,
  callbacks: RunEventStreamCallbacks,
  options: { after?: number } = {},
): EventSource {
  const url = new URL(runPath(runId, "/events"), window.location.origin);
  if (options.after !== undefined) {
    url.searchParams.set("after", String(options.after));
  }
  const source = new EventSource(url, { withCredentials: true });
  source.addEventListener("run-event", (message) => {
    try {
      callbacks.onEvent(JSON.parse((message as MessageEvent<string>).data) as RunEvent);
    } catch (error) {
      callbacks.onProtocolError?.(error);
    }
  });
  if (callbacks.onError !== undefined) {
    source.onerror = callbacks.onError;
  }
  return source;
}
