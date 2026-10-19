import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const CSS = readFileSync(fileURLToPath(new URL('../../index.css', import.meta.url)), 'utf8');

/** Pull the body of a top-level `selector { ... }` block. */
function block(selector: string): string {
  const start = CSS.indexOf(selector + ' {');
  expect(start, `missing ${selector} block`).toBeGreaterThan(-1);
  return CSS.slice(start, CSS.indexOf('\n}', start));
}

function tokens(css: string): Set<string> {
  return new Set([...css.matchAll(/^\s*(--[a-z0-9-]+):/gim)].map((m) => m[1]));
}

const DARK = tokens(block('[data-theme="dark"]'));
const LIGHT = tokens(block('[data-theme="light"]'));

// Colors that must resolve in both themes or one theme renders them as an
// undefined value (transparent text, invisible borders).
const REQUIRED = [
  '--danger', '--danger-strong', '--danger-bg', '--danger-border',
  '--success', '--success-strong', '--success-bg', '--success-border',
  '--warning', '--warning-strong', '--warning-bg', '--warning-border',
  '--info', '--info-strong', '--info-bg', '--info-border',
];

describe('theme tokens', () => {
  it('defines every status token in both themes', () => {
    const missingDark = REQUIRED.filter((t) => !DARK.has(t));
    const missingLight = REQUIRED.filter((t) => !LIGHT.has(t));
    expect(missingDark, `missing in :root: ${missingDark.join(', ')}`).toEqual([]);
    expect(missingLight, `missing in light theme: ${missingLight.join(', ')}`).toEqual([]);
  });

  it('keeps dark-only surface tokens out of the light theme override', () => {
    // --on-dark-* describe text on an always-dark well, so they are shared.
    const overridden = [...LIGHT].filter((t) => t.startsWith('--on-dark-'));
    expect(overridden).toEqual([]);
  });
});
