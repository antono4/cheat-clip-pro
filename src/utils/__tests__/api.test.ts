import { describe, expect, it } from 'vitest';
import { toErrorMessage } from '../api';

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
