// "" = same origin (the backend serves this app); the dev server sets http://127.0.0.1:8787 (next.config.ts)
export const API = process.env.NEXT_PUBLIC_API_URL || (typeof window !== "undefined" ? window.location.origin : "");
export const WS_URL = API.replace(/^http/, "ws") + "/ws";

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(API + path, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  get: <T>(p: string) => req<T>("GET", p),
  post: <T>(p: string, body?: unknown) => req<T>("POST", p, body ?? {}),
  patch: <T>(p: string, body: unknown) => req<T>("PATCH", p, body),
  del: <T>(p: string) => req<T>("DELETE", p),
};
