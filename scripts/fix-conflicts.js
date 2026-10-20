#!/usr/bin/env node
/**
 * fix-conflicts.js — find and repair leftover git conflict markers.
 *
 * A stray "<<<<<<< Updated upstream" is usually the remains of an interrupted
 * `git stash pop`, `git pull --rebase --autostash`, or a half-finished merge.
 * When it survives into a source file the bundler fails with a PARSE_ERROR, so
 * this script detects the markers and (with --fix) restores a clean tree.
 *
 * Cross-platform on purpose: the project ships to Windows (start.bat, update.bat)
 * and also runs on Linux/macOS, so a shell-only helper would not help everyone.
 *
 * Usage:
 *   node scripts/fix-conflicts.js           # report markers only (dry run)
 *   node scripts/fix-conflicts.js --fix     # back up local work, then reset
 */

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const SKIP_DIRS = new Set(['.git', 'node_modules', 'dist', 'dist-ssr', 'build', '.vite', '.cache']);
// Git emits exactly 7 characters for each marker. The angle markers are
// unambiguous; a bare "=======" is only meaningful alongside one of them, so
// files that contain just that line (e.g. comment separators) are not flagged.
const LEFT_RE = /^<<<<<<</;
const RIGHT_RE = /^>>>>>>>/;
const SEP_RE = /^=======$/;

function tryGit(args) {
  try {
    return execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  } catch {
    return null;
  }
}

/** Walk the working tree and return [{ file, lines: [n, ...] }] for files with markers. */
function findMarkers(root) {
  const hits = [];

  const walk = (dir) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (SKIP_DIRS.has(entry.name)) continue;
        walk(full);
      } else if (entry.isFile()) {
        let text;
        try {
          text = fs.readFileSync(full, 'utf8');
        } catch {
          continue; // binary or unreadable
        }
        const lines = [];
        let flagged = false;
        text.split('\n').forEach((line, i) => {
          if (LEFT_RE.test(line) || RIGHT_RE.test(line)) {
            flagged = true;
            lines.push(i + 1);
          } else if (SEP_RE.test(line)) {
            lines.push(i + 1); // reported only if an angle marker flags the file
          }
        });
        // A lone "=======" line is a common comment/heading separator, so only
        // treat a file as conflicted when a <<<<<<< or >>>>>>> marker is present.
        if (flagged) hits.push({ file: path.relative(root, full), lines });
      }
    }
  };

  walk(root);
  return hits;
}

function main() {
  const fix = process.argv.slice(2).includes('--fix');

  const root = tryGit(['rev-parse', '--show-toplevel']);
  if (!root) {
    console.error('Not inside a git repository.');
    process.exit(1);
  }
  console.log(`Repo: ${root}\n`);

  const hits = findMarkers(root);
  if (hits.length === 0) {
    console.log('OK: no conflict markers found. Nothing to fix.');
    process.exit(0);
  }

  console.log(`Found ${hits.length} file(s) with conflict markers:`);
  for (const { file, lines } of hits) {
    console.log(`  - ${file}`);
    console.log(`      lines: ${lines.join(', ')}`);
  }
  console.log();

  if (!fix) {
    console.log('Dry run only. Re-run with --fix to back up and reset these files.');
    process.exit(1);
  }

  // Abort any in-progress operation so the tree can be reset cleanly.
  const gitDir = tryGit(['rev-parse', '--git-dir']) || '.git';
  const gitDirAbs = path.isAbsolute(gitDir) ? gitDir : path.join(root, gitDir);
  const exists = (p) => fs.existsSync(path.join(gitDirAbs, p));
  const abort = (label, args) => {
    console.log(`${label}...`);
    tryGit(args);
  };
  if (exists('rebase-merge') || exists('rebase-apply')) abort('Aborting in-progress rebase', ['rebase', '--abort']);
  if (exists('MERGE_HEAD')) abort('Aborting in-progress merge', ['merge', '--abort']);
  if (exists('CHERRY_PICK_HEAD')) abort('Aborting in-progress cherry-pick', ['cherry-pick', '--abort']);

  // Snapshot the current (uncommitted) working tree onto a backup branch so no
  // local edit is lost. Staging everything first also captures untracked files.
  tryGit(['fetch', '--quiet', 'origin']);
  // "2026-10-19T14:12:44.123Z" -> "20261019-141244". A trailing "." would make
  // the branch name an invalid git ref, so the dot and milliseconds are dropped.
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
  const backup = `backup/pre-conflict-fix-${stamp}`;
  tryGit(['add', '-A']);
  const tree = tryGit(['write-tree']);
  if (tree) {
    const hasHead = tryGit(['rev-parse', '--verify', '-q', 'HEAD']);
    const args = ['commit-tree', tree, '-m', 'backup before conflict fix'];
    if (hasHead) args.push('-p', hasHead);
    const sha = tryGit(args);
    // `git branch -f` prints nothing on success, so check for null (failure),
    // not truthiness, or the empty stdout reads as a false negative.
    if (sha && tryGit(['branch', '-f', backup, sha]) !== null) {
      console.log(`Backup branch (holds your current, uncommitted edits): ${backup}`);
    } else {
      console.warn('WARNING: could not create a backup branch; local edits are only in the reflog.');
    }
  }

  // Reset the working tree to the remote default branch.
  const base = tryGit(['rev-parse', '--abbrev-ref', 'origin/HEAD']) || 'origin/master';
  console.log(`Resetting working tree to ${base} ...`);
  tryGit(['reset', '--hard', base]);

  const left = findMarkers(root);
  console.log();
  if (left.length === 0) {
    console.log('DONE: working tree is clean of conflict markers.');
    console.log(`Your previous edits are saved on branch '${backup}' if you need them:`);
    console.log(`  git --no-pager show ${backup}:<path>`);
    console.log(`  git branch -D ${backup}   # delete once you no longer need it`);
    process.exit(0);
  }
  console.error(`WARNING: ${left.length} file(s) still contain markers - resolve them manually.`);
  for (const { file } of left) console.error(`  - ${file}`);
  process.exit(2);
}

main();
