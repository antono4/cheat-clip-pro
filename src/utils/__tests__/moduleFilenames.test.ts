import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('../..', import.meta.url));

/** Walks src/ and returns every file path, relative to src/. */
function walk(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      walk(full, acc);
    } else {
      acc.push(relative(SRC, full));
    }
  }
  return acc;
}

describe('module filename case collisions', () => {
  // Windows and macOS use case-insensitive filesystems. Two modules whose names
  // differ only by case (e.g. LanguageContext.tsx + languageContext.ts) collapse
  // into one file there, and the resolver picks whichever extension it tries
  // first. That shipped a blank white screen: `./LanguageContext` resolved to
  // `languageContext.ts`, which does not export LanguageProvider.
  it('no two source files differ only by letter case', () => {
    const files = walk(SRC);

    const byLower = new Map<string, string[]>();
    for (const f of files) {
      const key = f.toLowerCase();
      byLower.set(key, [...(byLower.get(key) ?? []), f]);
    }

    const collisions = [...byLower.values()].filter((group) => group.length > 1);

    expect(
      collisions,
      `case-insensitive filename collisions break the app on Windows:\n` +
        collisions.map((g) => `  ${g.join('  <->  ')}`).join('\n'),
    ).toEqual([]);
  });

  it('no extensionless relative import resolves to two files on a case-insensitive filesystem', () => {
    // A bare `./Foo` import is resolved by trying extensions in order and, on a
    // case-insensitive filesystem, matching the stem case-insensitively. If two
    // files share a stem but differ by case (LanguageContext.tsx +
    // languageContext.ts), the resolver can pick the wrong one — that is exactly
    // how `./LanguageContext` bound to languageContext.ts and blanked the app.
    const files = walk(SRC).map((f) => f.replace(/\\/g, '/'));
    const byDir = new Map<string, string[]>();
    for (const f of files) {
      const dir = f.includes('/') ? f.slice(0, f.lastIndexOf('/')) : '.';
      byDir.set(dir, [...(byDir.get(dir) ?? []), f]);
    }

    const ambiguous: string[] = [];
    const importRe = /from\s+['"](\.[^'"]*)['"]/g;

    for (const rel of files) {
      const abs = join(SRC, rel);
      const source = readFileSync(abs, 'utf8');
      const dir = rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/')) : '.';
      const siblings = byDir.get(dir) ?? [];

      for (const match of source.matchAll(importRe)) {
        const spec = match[1];
        if (/\.[a-z]+$/i.test(spec)) continue; // explicit extension, unambiguous
        const base = spec.replace(/^\.\//, '').toLowerCase();

        const matches = siblings.filter((s) => {
          const name = s.slice(s.lastIndexOf('/') + 1);
          return name.slice(0, name.lastIndexOf('.')).toLowerCase() === base;
        });

        if (matches.length > 1) {
          ambiguous.push(`${rel}: '${spec}' -> ${matches.join(', ')}`);
        }
      }
    }

    expect(ambiguous, `ambiguous extensionless imports:\n${ambiguous.join('\n')}`).toEqual([]);
  });
});
