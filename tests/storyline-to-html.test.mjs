import assert from 'node:assert/strict';
import {
  link,
  lstat,
  mkdtemp,
  readFile,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  convertStoryLineFile,
  parseStoryLineJson,
  renderStoryLineHtml,
  StoryLineValidationError,
  validateStoryLine,
} from '../tools/storyline-to-html.mjs';

const devRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const cliPath = path.join(devRoot, 'tools/storyline-to-html.mjs');
const worldRef = { projectId: 'mygo-hogwarts', worldId: 'review-001' };
const firstLine = { rootSessionId: 'session-anon', topologyVersion: 1 };
const anonLine = { rootSessionId: 'session-anon', topologyVersion: 2 };
const tomoriLine = { rootSessionId: 'session-tomori', topologyVersion: 2 };

test('valid StoryLine JSON renders a horizontal worktree with concrete event text and transitions', () => {
  const value = fixture();
  assert.deepEqual(validateStoryLine(value), []);
  assert.deepEqual(parseStoryLineJson(JSON.stringify(value)), value);

  const html = renderStoryLineHtml(value, {
    agentLabels: { anon: '千早爱音', tomori: '高松灯' },
  });
  assert.match(html, /每一个点，都是一次真实发生/);
  assert.match(html, /千早爱音互动线 @ v1/);
  assert.match(html, /高松灯互动线 @ v2/);
  assert.match(html, /千早爱音 对 高松灯 说/);
  assert.match(html, /「灯，要不要一起试试这段魔咒？」/);
  assert.match(html, /同场角色：<\/strong>千早爱音、高松灯/);
  assert.match(html, /class="session-segment"/);
  assert.match(html, /class="transition-edge"/);
  assert.match(html, /class="timeline-event"/);
  assert.match(html, /split · membership change/);
  assert.match(html, /<b>变化前<\/b>/);
  assert.match(html, /<b>变化后<\/b>/);
  assert.match(html, /id="agent-filter"/);
  assert.match(html, /id="line-filter"/);
  assert.match(html, /id="kind-filter"/);
  assert.match(html, /id="next-event"/);
  assert.match(html, /Show raw canonical JSON/);
  assert.match(html, /Canonical JSON SHA-256 · [a-f0-9]{64}/);
  assert.doesNotMatch(html, /[ \t]+$/m);
});

test('a split child StoryLine remains visible as a graph segment before it receives an Entry', () => {
  const html = renderStoryLineHtml(fixture());
  assert.match(html, /session-tomori @ v2/);
  assert.equal((html.match(/class="session-segment"/g) ?? []).length, 3);
  assert.equal((html.match(/class="timeline-event"/g) ?? []).length, 2);
});

test('model-provided text and identifiers cannot inject HTML or attributes', () => {
  const maliciousAgentId = 'bad" autofocus onfocus="alert(2)';
  const value = JSON.parse(
    JSON.stringify(fixture()).replaceAll(
      JSON.stringify('tomori'),
      JSON.stringify(maliciousAgentId),
    ),
  );
  value.storyLines[0].entries[0].text = '</script><script>globalThis.PWNED = true</script><img src=x onerror=alert(1)>';

  const html = renderStoryLineHtml(value);
  assert.doesNotMatch(html, /<img src=x/);
  assert.doesNotMatch(html, /<script>globalThis\.PWNED/);
  assert.match(html, /&lt;\/script&gt;&lt;script&gt;globalThis\.PWNED/);
  assert.match(html, /bad&quot; autofocus onfocus=&quot;alert\(2\)/);
});

test('the standalone viewer has no external stylesheet, script, or network call', () => {
  const html = renderStoryLineHtml(fixture());
  assert.match(html, /Content-Security-Policy" content="default-src 'none'/);
  assert.doesNotMatch(html, /<script\s+[^>]*src=/i);
  assert.doesNotMatch(html, /<link\s+[^>]*href=/i);
  assert.doesNotMatch(html, /\bfetch\s*\(/);
  assert.doesNotMatch(html, /new\s+WebSocket\s*\(/);
});

test('the worktree expands to its full height without a nested clipping viewport', () => {
  const html = renderStoryLineHtml(fixture());
  assert.match(html, /body \{[^}]*overflow-x: auto;/);
  assert.match(html, /\.graph-scroll \{[^}]*height: auto;[^}]*overflow: visible;/s);
  assert.doesNotMatch(html, /height: min\(72vh, 900px\)/);
  assert.doesNotMatch(html, /graphScroll\.scrollTo/);
  assert.match(html, /event\.querySelector\('\.event-card'\)\.scrollIntoView/);
  assert.match(html, /agent\.value = '';\s+line\.value = '';\s+kind\.value = '';/);
});

test('rendering is byte-stable even when object property insertion order differs', () => {
  const value = fixture();
  const reordered = reverseObjectKeys(value);
  assert.deepEqual(reordered, value);
  assert.equal(renderStoryLineHtml(reordered), renderStoryLineHtml(value));
});

test('parsing reports malformed JSON and schema errors without accepting unknown fields', () => {
  assert.throws(
    () => parseStoryLineJson('{not-json', 'broken.json'),
    (error) => error instanceof StoryLineValidationError
      && error.diagnostics.some((item) => item.includes('Invalid JSON in broken.json')),
  );

  const value = fixture();
  delete value.status;
  value.privateMemory = 'must never enter StageView';
  assert.throws(
    () => parseStoryLineJson(JSON.stringify(value)),
    (error) => error instanceof StoryLineValidationError
      && error.diagnostics.some((item) => item.includes('status must be a non-empty string'))
      && error.diagnostics.some((item) => item.includes('privateMemory is not supported')),
  );
});

test('validation rejects duplicate Entries and commit positions across StoryLines', () => {
  const value = fixture();
  const duplicate = structuredClone(value.storyLines[0].entries[0]);
  duplicate.rootSessionIdAtCommit = tomoriLine.rootSessionId;
  duplicate.topologyVersion = tomoriLine.topologyVersion;
  value.storyLines[2].entries.push(duplicate);

  const diagnostics = validateStoryLine(value);
  assert.ok(diagnostics.some((item) => item.includes('entryId duplicates')));
  assert.ok(diagnostics.some((item) => item.includes('commitPosition duplicates')));
});

test('validation rejects broken links and links owned by another World', () => {
  const value = fixture();
  const link = value.storyLines[1].entries[0].links[0];
  link.relatedEntryId = 'missing-entry';
  link.worldRef = { ...worldRef, worldId: 'foreign-world' };

  const diagnostics = validateStoryLine(value);
  assert.ok(diagnostics.some((item) => item.includes('worldRef belongs to a different World')));
  assert.ok(diagnostics.some((item) => item.includes('references unknown Entry "missing-entry"')));
});

test('validation rejects Entry relation and StoryLine lineage cycles', () => {
  const entryCycle = fixture();
  entryCycle.storyLines[0].entries[0].links.push({
    worldRef,
    entryId: 'entry-dialogue-1',
    relationKind: 'cause',
    relatedEntryId: 'entry-transition-2',
    relationOrder: 0,
  });
  assert.ok(validateStoryLine(entryCycle).some((item) => item.includes('Entry relation graph contains a cycle')));

  const lineCycle = fixture();
  lineCycle.storyLines[0].parentLineKeys = [anonLine];
  lineCycle.storyLines[1].childLineKeys = [firstLine];
  assert.ok(validateStoryLine(lineCycle).some((item) => item.includes('StoryLine parent/child graph contains a cycle')));
});

test('validation rejects transition partitions that lose members or mismatch referenced lines', () => {
  const value = fixture();
  value.storyLines[1].entries[0].transition.afterParts[1].memberAgentIds = ['rana'];

  const diagnostics = validateStoryLine(value);
  assert.ok(diagnostics.some((item) => item.includes('must cover the same Agent set')));
  assert.ok(diagnostics.some((item) => item.includes('must equal the referenced StoryLine members')));
});

test('validation matches the StageView status, Entry-kind, visibility, and topology contract', () => {
  const badStatus = fixture();
  badStatus.status = 'banana';
  assert.ok(validateStoryLine(badStatus).some((item) => item.includes('status "banana" is not supported')));

  const missingDialogueFields = fixture();
  delete missingDialogueFields.storyLines[0].entries[0].targetAgentId;
  delete missingDialogueFields.storyLines[0].entries[0].deliveryChannel;
  const missingDiagnostics = validateStoryLine(missingDialogueFields);
  assert.ok(missingDiagnostics.some((item) => item.includes('targetAgentId is required for dialogue')));
  assert.ok(missingDiagnostics.some((item) => item.includes('deliveryChannel must be a non-empty string')));

  const foreignActor = fixture();
  foreignActor.storyLines[0].entries[0].actorAgentId = 'foreign-agent';
  foreignActor.storyLines[0].entries[0].recipientAgentIds.push('foreign-agent');
  assert.ok(validateStoryLine(foreignActor).some((item) => item.includes('actorAgentId must belong')));

  const foreignTarget = fixture();
  foreignTarget.storyLines[0].entries[0].targetAgentId = 'foreign-agent';
  assert.ok(validateStoryLine(foreignTarget).some((item) => item.includes('targetAgentId must belong')));

  const leakedRecipient = fixture();
  leakedRecipient.storyLines[0].entries[0].recipientAgentIds.push('foreign-agent');
  assert.ok(validateStoryLine(leakedRecipient).some((item) => item.includes('do not match StoryLine visibility')));

  const missingTransitionRecipient = fixture();
  missingTransitionRecipient.storyLines[1].entries[0].recipientAgentIds = ['anon'];
  assert.ok(validateStoryLine(missingTransitionRecipient).some((item) => item.includes('must equal all transition Agents')));

  const foreignTransitionTarget = fixture();
  foreignTransitionTarget.storyLines[1].entries[0].targetAgentId = 'foreign-agent';
  assert.ok(validateStoryLine(foreignTransitionTarget).some((item) => item.includes('targetAgentId must belong to the actor after part')));

  const badDate = fixture();
  badDate.storyLines[0].entries[0].occurredAt = '2026-02-31T10:00:00+08:00';
  assert.ok(validateStoryLine(badDate).some((item) => item.includes('ISO date-time with timezone')));

  const newerThanCommit = fixture();
  newerThanCommit.storyLines[0].entries[0].topologyVersion = 2;
  assert.ok(validateStoryLine(newerThanCommit).some((item) => item.includes('cannot be newer than its commitPosition')));
});

test('validation rejects phantom lineage and transition topology drift', () => {
  const phantom = fixture();
  phantom.storyLines[1].entries = [];
  phantom.builtThrough = { worldVersion: 1, entryIndex: 0 };
  assert.ok(validateStoryLine(phantom).some((item) => item.includes('no committed transition')));

  const drift = fixture();
  const driftKey = { rootSessionId: 'session-tomori', topologyVersion: 99 };
  drift.storyLines[0].childLineKeys[1] = driftKey;
  drift.storyLines[2].key = driftKey;
  drift.storyLines[2].parentLineKeys = [firstLine];
  drift.storyLines[1].entries[0].transition.afterParts[1].lineKey = driftKey;
  const diagnostics = validateStoryLine(drift);
  assert.ok(diagnostics.some((item) => item.includes('afterParts must share one topologyVersion')));

  const future = fixture();
  for (const part of future.storyLines[1].entries[0].transition.afterParts) {
    part.lineKey = { ...part.lineKey, topologyVersion: 3 };
  }
  assert.ok(validateStoryLine(future).some((item) => item.includes('afterParts topologyVersion must equal')));
});

test('malformed nested transition data returns diagnostics instead of throwing', () => {
  const value = fixture();
  value.storyLines[1].entries[0].transition.beforeParts = 'not-an-array';
  assert.doesNotThrow(() => validateStoryLine(value));
  assert.ok(validateStoryLine(value).some((item) => item.includes('beforeParts must be an array')));
});

test('file conversion creates a deterministic standalone HTML artifact', async (context) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'storyline-viewer-'));
  context.after(() => rm(directory, { recursive: true, force: true }));
  const inputPath = path.join(directory, 'storyline.json');
  const outputPath = path.join(directory, 'nested', 'storyline.html');
  await writeFile(inputPath, JSON.stringify(fixture()), 'utf8');

  const first = await convertStoryLineFile({ inputPath, outputPath });
  const firstBytes = await readFile(outputPath, 'utf8');
  const second = await convertStoryLineFile({ inputPath, outputPath });
  const secondBytes = await readFile(outputPath, 'utf8');
  assert.equal(first.outputPath, outputPath);
  assert.equal(second.html, first.html);
  assert.equal(secondBytes, firstBytes);
  assert.match(firstBytes, /千早爱音/);
  assert.doesNotMatch(firstBytes, /promptProfile|relationships|writePolicy/);
  assert.equal((await stat(outputPath)).mode & 0o777, 0o600);
});

test('file conversion rejects same-inode outputs and replaces output symlinks privately', async (context) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'storyline-viewer-files-'));
  context.after(() => rm(directory, { recursive: true, force: true }));
  const inputPath = path.join(directory, 'storyline.json');
  const hardLinkPath = path.join(directory, 'storyline-hardlink.html');
  const symbolicLinkPath = path.join(directory, 'storyline-symlink.html');
  await writeFile(inputPath, JSON.stringify(fixture()), 'utf8');
  await link(inputPath, hardLinkPath);
  await symlink(inputPath, symbolicLinkPath);

  await assert.rejects(
    convertStoryLineFile({ inputPath, outputPath: hardLinkPath }),
    /must not reference the same file/,
  );
  await assert.rejects(
    convertStoryLineFile({ inputPath, outputPath: symbolicLinkPath }),
    /must not reference the same file/,
  );

  const sentinelPath = path.join(directory, 'sentinel.txt');
  const outputPath = path.join(directory, 'replace-link.html');
  await writeFile(sentinelPath, 'do not overwrite', 'utf8');
  await symlink(sentinelPath, outputPath);
  await convertStoryLineFile({ inputPath, outputPath });

  assert.equal(await readFile(sentinelPath, 'utf8'), 'do not overwrite');
  assert.equal((await lstat(outputPath)).isSymbolicLink(), false);
  assert.equal((await stat(outputPath)).mode & 0o777, 0o600);
});

test('CLI writes valid HTML and returns non-zero for every invalid graph class', async (context) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'storyline-cli-'));
  context.after(() => rm(directory, { recursive: true, force: true }));
  const inputPath = path.join(directory, 'storyline.json');
  const outputPath = path.join(directory, 'storyline.html');
  await writeFile(inputPath, JSON.stringify(fixture()), 'utf8');

  const success = spawnSync(
    process.execPath,
    [cliPath, inputPath, '--out', outputPath],
    { cwd: devRoot, encoding: 'utf8' },
  );
  assert.equal(success.status, 0, success.stderr);
  assert.match(success.stdout, /✓ rendered/);
  assert.match(await readFile(outputPath, 'utf8'), /<!doctype html>/);

  const directoryShortcut = spawnSync(
    process.execPath,
    [cliPath, directory],
    { cwd: devRoot, encoding: 'utf8' },
  );
  assert.equal(directoryShortcut.status, 0, directoryShortcut.stderr);
  assert.match(directoryShortcut.stdout, /storyline\.json.*storyline\.html/);
  assert.match(await readFile(outputPath, 'utf8'), /<!doctype html>/);

  const duplicate = fixture();
  duplicate.storyLines[2].entries.push({
    ...structuredClone(duplicate.storyLines[0].entries[0]),
    rootSessionIdAtCommit: tomoriLine.rootSessionId,
    topologyVersion: tomoriLine.topologyVersion,
  });
  const brokenLink = fixture();
  brokenLink.storyLines[1].entries[0].links[0].relatedEntryId = 'missing';
  const crossWorldLink = fixture();
  crossWorldLink.storyLines[1].entries[0].links[0].worldRef = { ...worldRef, worldId: 'foreign' };
  const cycle = fixture();
  cycle.storyLines[0].entries[0].links.push({
    worldRef,
    entryId: 'entry-dialogue-1',
    relationKind: 'cause',
    relatedEntryId: 'entry-transition-2',
    relationOrder: 0,
  });
  const invalidCases = [
    [{ formatVersion: 1 }, /worldRef must be an object/],
    [duplicate, /entryId duplicates/],
    [brokenLink, /references unknown Entry/],
    [crossWorldLink, /belongs to a different World/],
    [cycle, /Entry relation graph contains a cycle/],
  ];
  for (const [invalid, expected] of invalidCases) {
    await writeFile(inputPath, JSON.stringify(invalid), 'utf8');
    const failure = spawnSync(
      process.execPath,
      [cliPath, inputPath, '--out', outputPath],
      { cwd: devRoot, encoding: 'utf8' },
    );
    assert.equal(failure.status, 1);
    assert.match(failure.stderr, /StoryLine validation failed/);
    assert.match(failure.stderr, expected);
  }
});

function fixture() {
  const firstEntry = {
    worldRef,
    entryId: 'entry-dialogue-1',
    entryKind: 'dialogue',
    commitPosition: { worldVersion: 1, entryIndex: 0 },
    rootSessionIdAtCommit: firstLine.rootSessionId,
    topologyVersion: firstLine.topologyVersion,
    actorAgentId: 'anon',
    occurredAt: '2026-09-11T10:00:00+08:00',
    text: '灯，要不要一起试试这段魔咒？',
    recipientAgentIds: ['anon', 'tomori'],
    targetAgentId: 'tomori',
    deliveryChannel: 'direct',
    links: [],
  };
  const transitionEntry = {
    worldRef,
    entryId: 'entry-transition-2',
    entryKind: 'session_transition',
    commitPosition: { worldVersion: 2, entryIndex: 0 },
    rootSessionIdAtCommit: anonLine.rootSessionId,
    topologyVersion: anonLine.topologyVersion,
    actorAgentId: 'anon',
    occurredAt: '2026-09-11T10:01:00+08:00',
    text: '爱音离开当前合奏，去取落在门边的谱纸。',
    recipientAgentIds: ['anon', 'tomori'],
    deliveryChannel: 'public',
    links: [{
      worldRef,
      entryId: 'entry-transition-2',
      relationKind: 'previous',
      relatedEntryId: 'entry-dialogue-1',
      relationOrder: 0,
    }],
    transition: {
      reason: 'split',
      beforeParts: [{ lineKey: firstLine, memberAgentIds: ['anon', 'tomori'] }],
      afterParts: [
        { lineKey: anonLine, memberAgentIds: ['anon'] },
        { lineKey: tomoriLine, memberAgentIds: ['tomori'] },
      ],
    },
  };
  return {
    formatVersion: 1,
    worldRef,
    builtThrough: { worldVersion: 2, entryIndex: 0 },
    status: 'paused',
    storyLines: [
      {
        key: firstLine,
        memberAgentIds: ['anon', 'tomori'],
        parentLineKeys: [],
        childLineKeys: [anonLine, tomoriLine],
        entries: [firstEntry],
      },
      {
        key: anonLine,
        memberAgentIds: ['anon'],
        parentLineKeys: [firstLine],
        childLineKeys: [],
        entries: [transitionEntry],
      },
      {
        key: tomoriLine,
        memberAgentIds: ['tomori'],
        parentLineKeys: [firstLine],
        childLineKeys: [],
        entries: [],
      },
    ],
  };
}

function reverseObjectKeys(value) {
  if (Array.isArray(value)) return value.map(reverseObjectKeys);
  if (value === null || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.entries(value).reverse().map(([key, item]) => [key, reverseObjectKeys(item)]),
  );
}
