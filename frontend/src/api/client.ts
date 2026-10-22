// The one place that talks to Django. Every request:
// - stays on this site (same origin, cookies included, no CORS);
// - sends JSON, and for changes the CSRF token from the `csrftoken` cookie in X-CSRFToken
//   (docs/API.md, "Security"). A refused token is fetched again and the request retried once;
//   a cross-site refusal is never retried.
// Errors become ApiError (the server answered) or OfflineError (it couldn't be reached).

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class OfflineError extends Error {
  constructor() {
    super("Can't reach the bot. Make sure Docker Desktop is open and the bot is started.");
    this.name = "OfflineError";
  }
}

export class ApiError extends Error {
  status: number;
  code: string;
  body: Record<string, unknown>;
  /** Errors per form field, e.g. {handle: ["…"]}. */
  fields: Record<string, string[]>;

  constructor(status: number, body: Record<string, unknown> | null) {
    const data = body ?? {};
    const fields = fieldErrors(data);
    super(messageFor(status, data, fields));
    this.name = "ApiError";
    this.status = status;
    this.code = typeof data.code === "string" ? data.code : "";
    this.body = data;
    this.fields = fields;
  }
}

function fieldErrors(body: Record<string, unknown>): Record<string, string[]> {
  // New-style errors carry {code, message, fields}; the account and channel forms keep
  // DRF's original {"field": ["message"]} shape (docs/API.md, "Errors").
  const source = (body.fields && typeof body.fields === "object" ? body.fields : body.code ? {} : body) as Record<
    string,
    unknown
  >;
  const out: Record<string, string[]> = {};
  for (const [key, value] of Object.entries(source)) {
    if (key !== "detail") flatten(key, value, out);
  }
  return out;
}

/** A list field's errors come per item ({destinations: [{}, {role_id: ["…"]}]}): they become
 * "destinations.1.role_id". Plain messages stay under their field. */
function flatten(key: string, value: unknown, out: Record<string, string[]>) {
  if (typeof value === "string") {
    (out[key] ??= []).push(value);
  } else if (Array.isArray(value)) {
    value.forEach((item, index) => (typeof item === "string" ? flatten(key, item, out) : flatten(`${key}.${index}`, item, out)));
  } else if (value && typeof value === "object") {
    for (const [inner, innerValue] of Object.entries(value)) flatten(`${key}.${inner}`, innerValue, out);
  }
}

function messageFor(status: number, body: Record<string, unknown>, fields: Record<string, string[]>): string {
  if (typeof body.message === "string" && body.message) return body.message;
  const first = Object.values(fields)[0]?.[0];
  if (first) return first;
  if (typeof body.detail === "string") return body.detail;
  if (status >= 500) return "Something went wrong on the bot's side. Try again in a moment.";
  return "That didn't work. Try again.";
}

function cookie(name: string): string {
  const match = document.cookie.split("; ").find((part) => part.startsWith(name + "="));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : "";
}

/** Always a full address on this site (never another host). */
export function onThisSite(path: string): string {
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin) throw new Error("The control panel only talks to its own bot.");
  return url.href;
}

let tokenRequest: Promise<string> | null = null;

async function freshToken(): Promise<string> {
  // The answer also sets the csrftoken cookie, which is what later requests read.
  tokenRequest ??= fetch(onThisSite("/api/csrf/"), { credentials: "same-origin", headers: { Accept: "application/json" } })
    .then((res) => res.json())
    .then((body: { csrf_token: string }) => body.csrf_token)
    .finally(() => {
      tokenRequest = null;
    });
  return tokenRequest;
}

async function csrfToken(): Promise<string> {
  return cookie("csrftoken") || freshToken();
}

/** Turn an absolute "next page" link from the API into a path on this site. */
export function apiPath(link: string): string {
  const url = new URL(link, window.location.origin);
  return url.pathname + url.search;
}

export async function request<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const unsafe = UNSAFE.has(method);
  if (unsafe) headers["X-CSRFToken"] = await csrfToken();
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(onThisSite(path), {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new OfflineError();
  }

  // A dev proxy or Docker answers 502/504 with HTML while the bot is starting or stopped.
  const isJson = (response.headers.get("Content-Type") ?? "").includes("json");
  if (!isJson && (response.status === 502 || response.status === 503 || response.status === 504)) {
    throw new OfflineError();
  }
  const data = response.status === 204 ? null : isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    const error = new ApiError(response.status, data);
    if (error.code === "csrf_failed" && unsafe && !retried) {
      await freshToken();
      return request<T>(method, path, body, true);
    }
    throw error;
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  delete: <T = null>(path: string) => request<T>("DELETE", path),
};

/** Read every page of a page-numbered list (accounts and channels come 20 at a time). */
export async function getAll<T>(path: string): Promise<T[]> {
  const items: T[] = [];
  let next: string | null = path;
  for (let page = 0; next && page < 100; page += 1) {
    const data: { results: T[]; next: string | null } = await api.get(next);
    items.push(...data.results);
    next = data.next ? apiPath(data.next) : null;
  }
  return items;
}

/** A plain-language message for any error thrown by the API layer. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof OfflineError) return error.message;
  return "Something unexpected happened. Reload the page and try again.";
}
