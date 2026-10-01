import type { Envelope, TokenPair } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const ACCESS_KEY = "sgdss.access";
const REFRESH_KEY = "sgdss.refresh";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export const tokens = {
  get access(): string | null {
    return typeof window === "undefined" ? null : window.localStorage.getItem(ACCESS_KEY);
  },
  get refresh(): string | null {
    return typeof window === "undefined" ? null : window.localStorage.getItem(REFRESH_KEY);
  },
  set(pair: Pick<TokenPair, "access_token" | "refresh_token">): void {
    window.localStorage.setItem(ACCESS_KEY, pair.access_token);
    window.localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  },
  clear(): void {
    window.localStorage.removeItem(ACCESS_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
  },
};

async function rawRequest(path: string, init: RequestInit, token: string | null): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${API_URL}${path}`, { ...init, headers });
}

async function tryRefresh(): Promise<boolean> {
  const refresh = tokens.refresh;
  if (!refresh) return false;
  const res = await rawRequest("/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token: refresh }) }, null);
  if (!res.ok) return false;
  const body = (await res.json()) as Envelope<TokenPair>;
  if (!body.data) return false;
  tokens.set(body.data);
  return true;
}

/** Calls the API, unwraps the {success,data,error} envelope, and refreshes the access token once on 401. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res = await rawRequest(path, init, tokens.access);
  if (res.status === 401 && !path.startsWith("/auth/") && (await tryRefresh())) {
    res = await rawRequest(path, init, tokens.access);
  }
  const body = (await res.json()) as Envelope<T>;
  if (!res.ok || !body.success) {
    throw new ApiError(res.status, body.error?.code ?? "error", body.error?.message ?? res.statusText);
  }
  return body.data as T;
}
