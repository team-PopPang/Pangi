import type { components, operations, paths } from "./generated";

export type BootstrapAdminRequest =
  operations["createBootstrapAdmin"]["requestBody"]["content"]["application/json"];
export type BootstrapAdminResponse =
  operations["createBootstrapAdmin"]["responses"][201]["content"]["application/json"];
export type LoginRequest = operations["login"]["requestBody"]["content"]["application/json"];
export type SessionEnvelope =
  operations["getAuthSession"]["responses"][200]["content"]["application/json"];
export type SessionInfo = SessionEnvelope["session"];

type ApiPath = keyof paths;
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

async function request<T>(path: ApiPath, options: RequestOptions): Promise<T> {
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
};
