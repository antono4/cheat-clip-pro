/**
 * Resilient API client for Cheat Clip Pro.
 * Handles automatic retry when backend server is starting up or reloading (503 / network refused).
 */

export interface ResilientFetchOptions extends RequestInit {
  maxRetries?: number;
  retryDelay?: number;
  silent?: boolean;
}

/**
 * Best-effort message extraction for values caught from `unknown`.
 * Non-Error rejections (strings, objects) fall back to the supplied default.
 */
export function toErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

export type AppUpdateOutcome =
  | { status: 'restarting' }
  | { status: 'up-to-date' }
  | { status: 'error'; message: string };

/**
 * Calls POST /api/system/update and classifies the outcome.
 *
 * A dropped connection is deliberately NOT reported as an error: the backend is
 * restarting, so fetch() rejecting with "Failed to fetch" is the expected happy
 * path, not a failure to show the user. Only a real HTTP error response (e.g.
 * 403 in server mode, 500 when the remote is unreachable) is surfaced.
 *
 * The fetch implementation is injectable so this stays testable without a DOM.
 */
export async function requestAppUpdate(fetchImpl: typeof fetch = fetch): Promise<AppUpdateOutcome> {
  try {
    const res = await fetchImpl('/api/system/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data: Record<string, unknown> = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail || data.message || 'Update failed';
      return { status: 'error', message: String(detail) };
    }
    if (data.status === 'up-to-date') {
      return { status: 'up-to-date' };
    }
    return { status: 'restarting' };
  } catch {
    // Connection dropped while the server restarted — treat as in-progress.
    return { status: 'restarting' };
  }
}

/**
 * Fetch wrapper with automatic retry on 502/503/504 or network connection errors.
 * Ideal for startup probes (/api/cookies, /api/hardware-accel, /api/models).
 */
export async function resilientFetch(
  input: RequestInfo | URL,
  options: ResilientFetchOptions = {}
): Promise<Response> {
  const {
    maxRetries = 4,
    retryDelay = 800,
    silent = false,
    ...fetchInit
  } = options;

  let lastError: unknown = null;
  let lastResponse: Response | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(input, fetchInit);

      // If backend is ready (status < 500 or not 502/503/504), return response
      if (response.status !== 503 && response.status !== 502 && response.status !== 504) {
        return response;
      }

      lastResponse = response;
      if (!silent) {
        console.warn(
          `[resilientFetch] Backend responded with HTTP ${response.status} for ${String(input)} (attempt ${attempt + 1}/${maxRetries + 1}). Retrying in ${retryDelay * Math.pow(1.5, attempt)}ms...`
        );
      }
    } catch (err: unknown) {
      lastError = err;
      if (!silent) {
        console.warn(
          `[resilientFetch] Network error for ${String(input)} (attempt ${attempt + 1}/${maxRetries + 1}):`,
          (err as Error)?.message || err
        );
      }
    }

    if (attempt < maxRetries) {
      const delay = Math.round(retryDelay * Math.pow(1.5, attempt));
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }

  if (lastResponse) {
    return lastResponse;
  }
  throw lastError || new Error(`Failed to fetch ${String(input)} after ${maxRetries + 1} attempts`);
}
