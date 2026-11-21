import type { ApiError } from "./types";

let tokenGetter: (() => Promise<string | null>) | null = null;
export function setTokenGetter(fn: (() => Promise<string | null>) | null) { tokenGetter = fn; }

export class HttpError extends Error {
  constructor(public status: number, public error: ApiError) { super(error.message); }
}

export async function authHeaders(): Promise<Record<string, string>> {
  const t = tokenGetter ? await tokenGetter() : null;
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(await authHeaders()), ...(init.headers as Record<string, string>) };
  if (init.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "same-origin" });
  if (!res.ok) {
    let err: ApiError = { code: "error", message: res.statusText, retryable: res.status >= 500,
      request_id: res.headers.get("x-request-id") || "" };
    try { err = (await res.json()).error ?? err; } catch { /* non-JSON */ }
    throw new HttpError(res.status, err);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}

export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
