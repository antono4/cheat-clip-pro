import { describe, expect, it } from 'vitest';
import { toErrorMessage, requestAppUpdate } from '../api';

describe('toErrorMessage', () => {
  it('returns the message of an Error', () => {
    expect(toErrorMessage(new Error('boom'), 'fallback')).toBe('boom');
  });

  it('returns a non-empty string rejection as-is', () => {
    expect(toErrorMessage('plain failure', 'fallback')).toBe('plain failure');
  });

  it('falls back for empty, nullish and non-Error values', () => {
    expect(toErrorMessage('', 'fallback')).toBe('fallback');
    expect(toErrorMessage(null, 'fallback')).toBe('fallback');
    expect(toErrorMessage(undefined, 'fallback')).toBe('fallback');
    expect(toErrorMessage({ detail: 'nope' }, 'fallback')).toBe('fallback');
  });

  it('falls back when an Error carries an empty message', () => {
    expect(toErrorMessage(new Error(''), 'fallback')).toBe('fallback');
  });

  it('passes an empty fallback through when nothing else is available', () => {
    expect(toErrorMessage(null, '')).toBe('');
  });
});

/** Builds a fetch double returning the given response. */
function fakeFetch(impl: () => Promise<Response>): typeof fetch {
  return (() => impl()) as unknown as typeof fetch;
}

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('requestAppUpdate', () => {
  it('treats a successful restart response as in-progress', async () => {
    const outcome = await requestAppUpdate(fakeFetch(async () => jsonResponse(200, { status: 'restarting' })));
    expect(outcome).toEqual({ status: 'restarting' });
  });

  it('treats a dropped connection ("Failed to fetch") as in-progress, not an error', async () => {
    const outcome = await requestAppUpdate(
      fakeFetch(async () => {
        throw new TypeError('Failed to fetch');
      })
    );
    expect(outcome).toEqual({ status: 'restarting' });
  });

  it('reports an up-to-date response without restarting', async () => {
    const outcome = await requestAppUpdate(fakeFetch(async () => jsonResponse(200, { status: 'up-to-date' })));
    expect(outcome).toEqual({ status: 'up-to-date' });
  });

  it('surfaces a real HTTP error (e.g. 403 in server mode)', async () => {
    const outcome = await requestAppUpdate(
      fakeFetch(async () => jsonResponse(403, { detail: 'Self-update is disabled' }))
    );
    expect(outcome).toEqual({ status: 'error', message: 'Self-update is disabled' });
  });

  it('falls back to a generic message when an error response has no detail', async () => {
    const outcome = await requestAppUpdate(fakeFetch(async () => jsonResponse(500, {})));
    expect(outcome).toEqual({ status: 'error', message: 'Update failed' });
  });
});
