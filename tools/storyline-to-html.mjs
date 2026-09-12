#!/usr/bin/env node

import { createHash, randomUUID } from 'node:crypto';
import { mkdir, open, readFile, realpath, rename, stat, unlink } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { renderWorktreeDocument } from './storyline-worktree-view.mjs';

const ENTRY_KINDS = new Set(['dialogue', 'action', 'behavior', 'session_transition']);
const LINK_KINDS = new Set(['previous', 'reply', 'cause']);
const TRANSITION_REASONS = new Set(['merge', 'split', 'transfer']);
const WORLD_STATUSES = new Set(['paused', 'running', 'ended']);
const REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLI_USAGE = 'Usage: node tools/storyline-to-html.mjs <artifact-directory|storyline.json> [--out <storyline.html>]';
const TOP_LEVEL_KEYS = new Set([
  'formatVersion',
  'worldRef',
  'builtThrough',
  'status',
  'storyLines',
]);
const LINE_KEYS = new Set([
  'key',
  'memberAgentIds',
  'parentLineKeys',
  'childLineKeys',
  'entries',
]);
const ENTRY_KEYS = new Set([
  'worldRef',
  'entryId',
  'entryKind',
  'commitPosition',
  'rootSessionIdAtCommit',
  'topologyVersion',
  'actorAgentId',
  'occurredAt',
  'text',
  'recipientAgentIds',
  'links',
  'targetAgentId',
  'targetObjectId',
  'operationId',
  'deliveryChannel',
  'transition',
]);

export class StoryLineValidationError extends Error {
  constructor(diagnostics) {
    super(`StoryLine validation failed with ${diagnostics.length} error(s)`);
    this.name = 'StoryLineValidationError';
    this.diagnostics = diagnostics;
  }
}

export function parseStoryLineJson(source, sourceName = 'StoryLine JSON') {
  let value;
  try {
    value = JSON.parse(source);
  } catch (error) {
    throw new StoryLineValidationError([`Invalid JSON in ${sourceName}: ${error.message}`]);
  }

  const diagnostics = validateStoryLine(value);
  if (diagnostics.length > 0) throw new StoryLineValidationError(diagnostics);
  return value;
}

export function validateStoryLine(value) {
  return inspectStoryLine(value).diagnostics;
}

export function renderStoryLineHtml(value, { agentLabels = {} } = {}) {
  const inspection = inspectStoryLine(value);
  if (inspection.diagnostics.length > 0) {
    throw new StoryLineValidationError(inspection.diagnostics);
  }

  const canonicalJson = canonicalStringify(value, 2);
  const artifactHash = createHash('sha256').update(canonicalJson).digest('hex');
  return renderWorktreeDocument({ value, inspection, canonicalJson, artifactHash, agentLabels });
}

export async function convertStoryLineFile({ inputPath, outputPath, agentLabels }) {
  const resolvedInput = path.resolve(inputPath);
  const resolvedOutput = path.resolve(outputPath);
  if (resolvedInput === resolvedOutput) {
    throw new Error('Input JSON and output HTML must use different paths');
  }

  let source;
  let inputStats;
  try {
    inputStats = await stat(resolvedInput);
    source = await readFile(resolvedInput, 'utf8');
  } catch (error) {
    throw new Error(`Cannot read StoryLine JSON ${resolvedInput}: ${error.message}`);
  }

  const outputStats = await statIfExists(resolvedOutput);
  if (outputStats && inputStats.dev === outputStats.dev && inputStats.ino === outputStats.ino) {
    throw new Error('Input JSON and output HTML must not reference the same file');
  }

  const value = parseStoryLineJson(source, resolvedInput);
  const labels = agentLabels ?? await loadProjectAgentLabels(value.worldRef.projectId);
  const html = renderStoryLineHtml(value, { agentLabels: labels });
  await writePrivateAtomic(resolvedOutput, html);
  return { inputPath: resolvedInput, outputPath: resolvedOutput, html };
}

async function loadProjectAgentLabels(projectId) {
  const projectsRoot = path.join(REPOSITORY_ROOT, 'projects');
  const manifestPath = path.resolve(projectsRoot, projectId, 'agents.json');
  if (!manifestPath.startsWith(`${projectsRoot}${path.sep}`)) return {};

  let resolvedManifest;
  let source;
  try {
    const resolvedProjects = await realpath(projectsRoot);
    resolvedManifest = await realpath(manifestPath);
    if (!resolvedManifest.startsWith(`${resolvedProjects}${path.sep}`)) return {};
    source = await readFile(resolvedManifest, 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return {};
    throw new Error(`Cannot read Agent manifest ${manifestPath}: ${error.message}`);
  }

  let manifest;
  try {
    manifest = JSON.parse(source);
  } catch (error) {
    throw new Error(`Invalid Agent manifest JSON ${resolvedManifest}: ${error.message}`);
  }
  if (manifest?.projectId !== projectId || !Array.isArray(manifest.agents)) return {};

  return Object.fromEntries(manifest.agents
    .filter((agent) => (
      typeof agent?.id === 'string'
      && agent.id.length > 0
      && typeof agent.displayName === 'string'
      && agent.displayName.length > 0
    ))
    .map((agent) => [agent.id, agent.displayName]));
}

function inspectStoryLine(value) {
  const diagnostics = [];
  const lines = [];
  const lineByKey = new Map();
  const entries = [];
  const entryById = new Map();
  const positionByKey = new Map();
  const agentIds = new Set();
  const entryKinds = new Set();

  if (!requireObject(value, 'root', diagnostics)) {
    return { diagnostics, lines, entries, agentIds, entryKinds };
  }
  rejectUnknownKeys(value, TOP_LEVEL_KEYS, 'root', diagnostics);
  if (value.formatVersion !== 1) diagnostics.push('formatVersion must equal 1');
  const topWorld = validateWorldRef(value.worldRef, 'worldRef', diagnostics);
  const builtThrough = value.builtThrough === null
    ? null
    : validatePosition(value.builtThrough, 'builtThrough', diagnostics);
  const status = requireString(value.status, 'status', diagnostics);
  if (status && !WORLD_STATUSES.has(status)) {
    diagnostics.push(`status "${status}" is not supported`);
  }
  if (!requireArray(value.storyLines, 'storyLines', diagnostics)) {
    return { diagnostics, lines, entries, agentIds, entryKinds };
  }
  if (value.storyLines.length === 0) diagnostics.push('storyLines must contain at least one line');

  for (const [lineIndex, line] of value.storyLines.entries()) {
    const location = `storyLines[${lineIndex}]`;
    if (!requireObject(line, location, diagnostics)) continue;
    rejectUnknownKeys(line, LINE_KEYS, location, diagnostics);
    const key = validateLineKey(line.key, `${location}.key`, diagnostics);
    const keyId = key ? encodeLineKey(key) : null;
    const members = validateUniqueStrings(
      line.memberAgentIds,
      `${location}.memberAgentIds`,
      diagnostics,
      { nonEmpty: true },
    );
    const parentKeys = validateLineKeys(line.parentLineKeys, `${location}.parentLineKeys`, diagnostics);
    const childKeys = validateLineKeys(line.childLineKeys, `${location}.childLineKeys`, diagnostics);
    requireArray(line.entries, `${location}.entries`, diagnostics);

    const record = { line, lineIndex, location, key, keyId, members, parentKeys, childKeys, entries: [] };
    lines.push(record);
    if (keyId) {
      if (lineByKey.has(keyId)) diagnostics.push(`${location}.key duplicates ${lineByKey.get(keyId).location}.key`);
      else lineByKey.set(keyId, record);
    }
    for (const member of members ?? []) agentIds.add(member);

    let previousPosition = null;
    for (const [entryIndex, entry] of arrayEntries(line.entries)) {
      const entryRecord = validateEntry({
        entry,
        location: `${location}.entries[${entryIndex}]`,
        lineRecord: record,
        topWorld,
        diagnostics,
      });
      if (!entryRecord) continue;
      record.entries.push(entryRecord);
      entries.push(entryRecord);
      entryKinds.add(entryRecord.entry.entryKind);
      for (const id of entryRecord.entry.recipientAgentIds) agentIds.add(id);
      agentIds.add(entryRecord.entry.actorAgentId);

      if (entryById.has(entryRecord.entry.entryId)) {
        diagnostics.push(`${entryRecord.location}.entryId duplicates ${entryById.get(entryRecord.entry.entryId).location}.entryId`);
      } else {
        entryById.set(entryRecord.entry.entryId, entryRecord);
      }
      const positionId = encodePosition(entryRecord.position);
      if (positionByKey.has(positionId)) {
        diagnostics.push(`${entryRecord.location}.commitPosition duplicates ${positionByKey.get(positionId)}`);
      } else {
        positionByKey.set(positionId, entryRecord.location);
      }
      if (previousPosition && comparePositions(previousPosition, entryRecord.position) >= 0) {
        diagnostics.push(`${entryRecord.location}.commitPosition must be later than the previous Entry in its StoryLine`);
      }
      previousPosition = entryRecord.position;
    }
  }

  const declaredEdges = validateLineGraph(lines, lineByKey, diagnostics);
  validateEntryGraph(entries, entryById, diagnostics);
  const transitionEdges = validateTransitions(entries, lineByKey, diagnostics);
  for (const edge of declaredEdges) {
    if (!transitionEdges.has(edge)) {
      diagnostics.push('StoryLine lineage contains an edge with no committed transition');
    }
  }
  for (const edge of transitionEdges) {
    if (!declaredEdges.has(edge)) {
      diagnostics.push('committed transition is missing its StoryLine lineage edge');
    }
  }
  validateBuiltThrough(entries, builtThrough, diagnostics);
  return { diagnostics, lines, entries, lineByKey, entryById, agentIds, entryKinds };
}

function validateEntry({ entry, location, lineRecord, topWorld, diagnostics }) {
  if (!requireObject(entry, location, diagnostics)) return null;
  rejectUnknownKeys(entry, ENTRY_KEYS, location, diagnostics);
  const world = validateWorldRef(entry.worldRef, `${location}.worldRef`, diagnostics);
  if (world && topWorld && !sameWorld(world, topWorld)) {
    diagnostics.push(`${location}.worldRef belongs to a different World`);
  }
  const entryId = requireString(entry.entryId, `${location}.entryId`, diagnostics);
  const kind = requireString(entry.entryKind, `${location}.entryKind`, diagnostics);
  if (kind && !ENTRY_KINDS.has(kind)) diagnostics.push(`${location}.entryKind "${kind}" is not supported`);
  const position = validatePosition(entry.commitPosition, `${location}.commitPosition`, diagnostics);
  const rootId = requireString(entry.rootSessionIdAtCommit, `${location}.rootSessionIdAtCommit`, diagnostics);
  const topology = requireInteger(entry.topologyVersion, `${location}.topologyVersion`, diagnostics, 1);
  const actor = requireString(entry.actorAgentId, `${location}.actorAgentId`, diagnostics);
  const occurredAt = requireString(entry.occurredAt, `${location}.occurredAt`, diagnostics);
  if (occurredAt && !isValidAwareDateTime(occurredAt)) {
    diagnostics.push(`${location}.occurredAt must be an ISO date-time with timezone`);
  }
  requireString(entry.text, `${location}.text`, diagnostics);
  const recipients = validateUniqueStrings(
    entry.recipientAgentIds,
    `${location}.recipientAgentIds`,
    diagnostics,
    { nonEmpty: true },
  );
  if (actor && recipients && !recipients.includes(actor)) {
    diagnostics.push(`${location}.recipientAgentIds must include actorAgentId`);
  }
  const delivery = requireString(entry.deliveryChannel, `${location}.deliveryChannel`, diagnostics);
  for (const optional of ['targetAgentId', 'targetObjectId', 'operationId']) {
    if (entry[optional] !== undefined) requireString(entry[optional], `${location}.${optional}`, diagnostics);
  }
  if (position && topology && topology > position.worldVersion) {
    diagnostics.push(`${location}.topologyVersion cannot be newer than its commitPosition`);
  }
  if (rootId && topology && lineRecord.key
      && (rootId !== lineRecord.key.rootSessionId || topology !== lineRecord.key.topologyVersion)) {
    diagnostics.push(`${location} belongs to a different StoryLine key`);
  }
  if (actor && lineRecord.members && !lineRecord.members.includes(actor)) {
    diagnostics.push(`${location}.actorAgentId must belong to its StoryLine members`);
  }

  const links = [];
  if (requireArray(entry.links, `${location}.links`, diagnostics)) {
    const seenLinks = new Set();
    const orders = new Map();
    for (const [linkIndex, link] of entry.links.entries()) {
      const linkLocation = `${location}.links[${linkIndex}]`;
      if (!requireObject(link, linkLocation, diagnostics)) continue;
      rejectUnknownKeys(
        link,
        new Set(['worldRef', 'entryId', 'relationKind', 'relatedEntryId', 'relationOrder']),
        linkLocation,
        diagnostics,
      );
      const linkWorld = validateWorldRef(link.worldRef, `${linkLocation}.worldRef`, diagnostics);
      if (linkWorld && topWorld && !sameWorld(linkWorld, topWorld)) {
        diagnostics.push(`${linkLocation}.worldRef belongs to a different World`);
      }
      const ownerId = requireString(link.entryId, `${linkLocation}.entryId`, diagnostics);
      const relationKind = requireString(link.relationKind, `${linkLocation}.relationKind`, diagnostics);
      const relatedId = requireString(link.relatedEntryId, `${linkLocation}.relatedEntryId`, diagnostics);
      const relationOrder = requireInteger(link.relationOrder, `${linkLocation}.relationOrder`, diagnostics, 0);
      if (ownerId && entryId && ownerId !== entryId) diagnostics.push(`${linkLocation}.entryId must equal its owning Entry`);
      if (relationKind && !LINK_KINDS.has(relationKind)) diagnostics.push(`${linkLocation}.relationKind "${relationKind}" is not supported`);
      if (relatedId && entryId && relatedId === entryId) diagnostics.push(`${linkLocation} cannot link an Entry to itself`);
      if (relationKind && relatedId) {
        const duplicateKey = `${relationKind}\0${relatedId}`;
        if (seenLinks.has(duplicateKey)) diagnostics.push(`${linkLocation} duplicates relation to "${relatedId}"`);
        seenLinks.add(duplicateKey);
      }
      if (relationKind && relationOrder !== null) {
        const values = orders.get(relationKind) ?? [];
        values.push(relationOrder);
        orders.set(relationKind, values);
      }
      links.push({ link, location: linkLocation, relationKind, relatedId, relationOrder });
    }
    for (const [relationKind, values] of orders) {
      const sorted = [...values].sort((left, right) => left - right);
      if (sorted.some((value, index) => value !== index)) {
        diagnostics.push(`${location}.links relationOrder for "${relationKind}" must be contiguous from 0`);
      }
    }
  }

  if (kind === 'session_transition') {
    validateTransitionShape(
      entry.transition,
      `${location}.transition`,
      diagnostics,
      position,
    );
  } else if (entry.transition !== undefined) {
    diagnostics.push(`${location}.transition is only valid for session_transition Entries`);
  }

  validateEntryKindFields({
    entry,
    kind,
    delivery,
    actor,
    recipients,
    lineMembers: lineRecord.members,
    location,
    diagnostics,
  });

  if (!(entryId && kind && position && rootId && topology && actor && recipients && delivery)) {
    return null;
  }
  return { entry, location, position, links, lineRecord };
}

function validateEntryKindFields({
  entry,
  kind,
  delivery,
  actor,
  recipients,
  lineMembers,
  location,
  diagnostics,
}) {
  const targetAgent = typeof entry.targetAgentId === 'string' && entry.targetAgentId.trim()
    ? entry.targetAgentId
    : null;
  const targetObject = typeof entry.targetObjectId === 'string' && entry.targetObjectId.trim()
    ? entry.targetObjectId
    : null;
  const operation = typeof entry.operationId === 'string' && entry.operationId.trim()
    ? entry.operationId
    : null;
  const hasTransition = entry.transition !== undefined;
  const forbidden = (condition, field) => {
    if (condition) diagnostics.push(`${location}.${field} is not valid for ${kind} Entries`);
  };

  if (kind === 'dialogue') {
    if (!targetAgent) diagnostics.push(`${location}.targetAgentId is required for dialogue Entries`);
    forbidden(targetObject !== null, 'targetObjectId');
    forbidden(operation !== null, 'operationId');
    forbidden(hasTransition, 'transition');
    if (delivery && !new Set(['direct', 'whisper']).has(delivery)) {
      diagnostics.push(`${location}.deliveryChannel must be direct or whisper for dialogue Entries`);
    }
  } else if (kind === 'action') {
    forbidden(targetAgent !== null, 'targetAgentId');
    if (!targetObject) diagnostics.push(`${location}.targetObjectId is required for action Entries`);
    if (!operation) diagnostics.push(`${location}.operationId is required for action Entries`);
    forbidden(hasTransition, 'transition');
    if (delivery && delivery !== 'public') {
      diagnostics.push(`${location}.deliveryChannel must be public for action Entries`);
    }
  } else if (kind === 'behavior') {
    forbidden(targetAgent !== null, 'targetAgentId');
    forbidden(targetObject !== null, 'targetObjectId');
    if (!operation) diagnostics.push(`${location}.operationId is required for behavior Entries`);
    forbidden(hasTransition, 'transition');
    if (delivery && delivery !== 'public') {
      diagnostics.push(`${location}.deliveryChannel must be public for behavior Entries`);
    }
  } else if (kind === 'session_transition') {
    forbidden(targetObject !== null, 'targetObjectId');
    forbidden(operation !== null, 'operationId');
    if (!hasTransition) diagnostics.push(`${location}.transition is required for session_transition Entries`);
    if (delivery && delivery !== 'public') {
      diagnostics.push(`${location}.deliveryChannel must be public for session_transition Entries`);
    }
    const reason = isPlainObject(entry.transition) ? entry.transition.reason : null;
    if (reason === 'split' && targetAgent) {
      diagnostics.push(`${location}.targetAgentId is not valid for split Entries`);
    } else if ((reason === 'merge' || reason === 'transfer') && !targetAgent) {
      diagnostics.push(`${location}.targetAgentId is required for ${reason} Entries`);
    }
  }

  if (!(actor && recipients && lineMembers) || kind === 'session_transition') return;
  if (kind === 'dialogue' && targetAgent && !lineMembers.includes(targetAgent)) {
    diagnostics.push(`${location}.targetAgentId must belong to its StoryLine members`);
  }
  const expectedRecipients = kind === 'dialogue' && delivery === 'whisper'
    ? [actor, targetAgent].filter(Boolean)
    : lineMembers;
  if (!sameStringSet(recipients, expectedRecipients)) {
    diagnostics.push(`${location}.recipientAgentIds do not match StoryLine visibility`);
  }
}

function validateLineGraph(lines, lineByKey, diagnostics) {
  const declaredEdges = new Set();
  for (const line of lines) {
    if (!line.keyId) continue;
    for (const parentKey of line.parentKeys ?? []) {
      const parent = lineByKey.get(encodeLineKey(parentKey));
      if (!parent) {
        diagnostics.push(`${line.location}.parentLineKeys references an unknown StoryLine ${lineLabel(parentKey)}`);
        continue;
      }
      if (parent.keyId === line.keyId) diagnostics.push(`${line.location} cannot be its own parent`);
      if (parent.key && line.key
          && parent.key.topologyVersion >= line.key.topologyVersion) {
        diagnostics.push(`${line.location} parent topology must be older than child topology`);
      }
      if (!(parent.childKeys ?? []).some((key) => encodeLineKey(key) === line.keyId)) {
        diagnostics.push(`${line.location} parent ${lineLabel(parentKey)} does not contain the reciprocal child link`);
      }
      if (!intersects(parent.members ?? [], line.members ?? [])) {
        diagnostics.push(`${line.location} parent ${lineLabel(parentKey)} shares no members with its child`);
      }
      declaredEdges.add(encodeLineageEdge(parent.keyId, line.keyId));
    }
    for (const childKey of line.childKeys ?? []) {
      const child = lineByKey.get(encodeLineKey(childKey));
      if (!child) {
        diagnostics.push(`${line.location}.childLineKeys references an unknown StoryLine ${lineLabel(childKey)}`);
        continue;
      }
      if (child.keyId === line.keyId) diagnostics.push(`${line.location} cannot be its own child`);
      if (!(child.parentKeys ?? []).some((key) => encodeLineKey(key) === line.keyId)) {
        diagnostics.push(`${line.location} child ${lineLabel(childKey)} does not contain the reciprocal parent link`);
      }
    }
  }
  detectCycle(
    lines.filter((line) => line.keyId),
    (line) => line.keyId,
    (line) => (line.childKeys ?? []).map((key) => lineByKey.get(encodeLineKey(key))).filter(Boolean),
    'StoryLine parent/child graph contains a cycle',
    diagnostics,
  );
  return declaredEdges;
}

function validateEntryGraph(entries, entryById, diagnostics) {
  for (const entry of entries) {
    for (const link of entry.links) {
      if (!link.relatedId) continue;
      const related = entryById.get(link.relatedId);
      if (!related) {
        diagnostics.push(`${link.location}.relatedEntryId references unknown Entry "${link.relatedId}"`);
        continue;
      }
      if (comparePositions(related.position, entry.position) >= 0) {
        diagnostics.push(`${link.location}.relatedEntryId must reference an earlier committed Entry`);
      }
    }
  }
  detectCycle(
    entries,
    (entry) => entry.entry.entryId,
    (entry) => entry.links.map((link) => entryById.get(link.relatedId)).filter(Boolean),
    'Entry relation graph contains a cycle',
    diagnostics,
  );
}

function validateTransitions(entries, lineByKey, diagnostics) {
  const transitionEdges = new Set();
  for (const record of entries) {
    if (record.entry.entryKind !== 'session_transition' || !isPlainObject(record.entry.transition)) continue;
    const transition = record.entry.transition;
    if (!Array.isArray(transition.beforeParts) || !Array.isArray(transition.afterParts)) continue;
    const before = transition.beforeParts.filter(isValidTransitionPart).map((part) => ({
      part,
      line: lineByKey.get(encodeLineKey(part.lineKey)),
    }));
    const after = transition.afterParts.filter(isValidTransitionPart).map((part) => ({
      part,
      line: lineByKey.get(encodeLineKey(part.lineKey)),
    }));
    for (const [side, parts] of [['beforeParts', before], ['afterParts', after]]) {
      for (const [index, item] of parts.entries()) {
        const location = `${record.location}.transition.${side}[${index}]`;
        if (!item.line) {
          diagnostics.push(`${location}.lineKey references an unknown StoryLine ${lineLabel(item.part.lineKey)}`);
        } else if (!sameStringSet(item.part.memberAgentIds, item.line.members)) {
          diagnostics.push(`${location}.memberAgentIds must equal the referenced StoryLine members`);
        }
      }
    }

    const currentKey = record.lineRecord.keyId;
    const actorPart = after.find((item) => encodeLineKey(item.part.lineKey) === currentKey);
    if (!actorPart) diagnostics.push(`${record.location}.transition.afterParts must contain the Entry StoryLine`);
    else if (!actorPart.part.memberAgentIds.includes(record.entry.actorAgentId)) {
      diagnostics.push(`${record.location}.transition actor must belong to the Entry after part`);
    }
    const affectedAgents = flatMembers(before.map((item) => item.part));
    if (!sameStringSet(record.entry.recipientAgentIds, affectedAgents)) {
      diagnostics.push(`${record.location}.recipientAgentIds must equal all transition Agents`);
    }
    if (record.entry.targetAgentId && actorPart
        && !actorPart.part.memberAgentIds.includes(record.entry.targetAgentId)) {
      diagnostics.push(`${record.location}.targetAgentId must belong to the actor after part`);
    }

    for (const beforePart of before) {
      for (const afterPart of after) {
        if (!(beforePart.line && afterPart.line)) continue;
        if (!intersects(beforePart.part.memberAgentIds, afterPart.part.memberAgentIds)) continue;
        const childId = afterPart.line.keyId;
        const parentId = beforePart.line.keyId;
        transitionEdges.add(encodeLineageEdge(parentId, childId));
        if (!(beforePart.line.childKeys ?? []).some((key) => encodeLineKey(key) === childId)
            || !(afterPart.line.parentKeys ?? []).some((key) => encodeLineKey(key) === parentId)) {
          diagnostics.push(
            `${record.location}.transition is missing lineage edge ${lineLabel(beforePart.part.lineKey)} -> ${lineLabel(afterPart.part.lineKey)}`,
          );
        }
      }
    }
  }
  return transitionEdges;
}

function validateTransitionShape(transition, location, diagnostics, commitPosition) {
  if (!requireObject(transition, location, diagnostics)) return;
  rejectUnknownKeys(transition, new Set(['reason', 'beforeParts', 'afterParts']), location, diagnostics);
  const reason = requireString(transition.reason, `${location}.reason`, diagnostics);
  if (reason && !TRANSITION_REASONS.has(reason)) diagnostics.push(`${location}.reason "${reason}" is not supported`);
  const before = validateTransitionParts(transition.beforeParts, `${location}.beforeParts`, diagnostics);
  const after = validateTransitionParts(transition.afterParts, `${location}.afterParts`, diagnostics);
  if (!(before && after)) return;
  if (!sameStringSet(flatMembers(before), flatMembers(after))) {
    diagnostics.push(`${location} beforeParts and afterParts must cover the same Agent set`);
  }
  const signatures = (parts) => new Set(parts.map((part) => [...part.memberAgentIds].sort().join('\0')));
  const beforeSignatures = signatures(before);
  const afterSignatures = signatures(after);
  if (beforeSignatures.size === afterSignatures.size
      && [...beforeSignatures].every((item) => afterSignatures.has(item))) {
    diagnostics.push(`${location} must change the EventSession partition`);
  }
  const expectedPartCounts = {
    merge: [2, 1],
    split: [1, 2],
    transfer: [2, 2],
  };
  const counts = expectedPartCounts[reason];
  if (counts && (before.length !== counts[0] || after.length !== counts[1])) {
    diagnostics.push(`${location}.${reason} requires ${counts[0]} before part(s) and ${counts[1]} after part(s)`);
  }
  const afterVersions = new Set(after.map((part) => part.lineKey.topologyVersion));
  if (afterVersions.size !== 1) {
    diagnostics.push(`${location}.afterParts must share one topologyVersion`);
    return;
  }
  const afterVersion = [...afterVersions][0];
  if (before.some((part) => part.lineKey.topologyVersion >= afterVersion)) {
    diagnostics.push(`${location}.beforeParts topologyVersion must be older than afterParts`);
  }
  if (commitPosition && afterVersion !== commitPosition.worldVersion) {
    diagnostics.push(`${location}.afterParts topologyVersion must equal the Entry commit worldVersion`);
  }
}

function validateTransitionParts(value, location, diagnostics) {
  if (!requireArray(value, location, diagnostics)) return null;
  if (value.length === 0) diagnostics.push(`${location} must contain at least one part`);
  const parts = [];
  const seenLines = new Set();
  const seenMembers = new Set();
  for (const [index, part] of value.entries()) {
    const partLocation = `${location}[${index}]`;
    if (!requireObject(part, partLocation, diagnostics)) continue;
    rejectUnknownKeys(part, new Set(['lineKey', 'memberAgentIds']), partLocation, diagnostics);
    const lineKey = validateLineKey(part.lineKey, `${partLocation}.lineKey`, diagnostics);
    const members = validateUniqueStrings(
      part.memberAgentIds,
      `${partLocation}.memberAgentIds`,
      diagnostics,
      { nonEmpty: true },
    );
    if (!(lineKey && members)) continue;
    const lineId = encodeLineKey(lineKey);
    if (seenLines.has(lineId)) diagnostics.push(`${partLocation}.lineKey duplicates another part on the same side`);
    seenLines.add(lineId);
    for (const member of members) {
      if (seenMembers.has(member)) diagnostics.push(`${partLocation}.memberAgentIds overlaps another part at "${member}"`);
      seenMembers.add(member);
    }
    parts.push({ lineKey, memberAgentIds: members });
  }
  return parts;
}

function validateBuiltThrough(entries, builtThrough, diagnostics) {
  if (entries.length === 0) {
    if (builtThrough !== null) diagnostics.push('builtThrough must be null when no committed Entry exists');
    return;
  }
  if (!builtThrough) {
    diagnostics.push('builtThrough is required when committed Entries exist');
    return;
  }
  const latest = entries.reduce((result, entry) => (
    comparePositions(result.position, entry.position) < 0 ? entry : result
  ));
  if (comparePositions(latest.position, builtThrough) !== 0) {
    diagnostics.push('builtThrough must equal the greatest committed Entry position');
  }
}

function validateLineKeys(value, location, diagnostics) {
  if (!requireArray(value, location, diagnostics)) return null;
  const result = [];
  const seen = new Set();
  for (const [index, key] of value.entries()) {
    const parsed = validateLineKey(key, `${location}[${index}]`, diagnostics);
    if (!parsed) continue;
    const encoded = encodeLineKey(parsed);
    if (seen.has(encoded)) diagnostics.push(`${location}[${index}] duplicates another line key`);
    seen.add(encoded);
    result.push(parsed);
  }
  return result;
}

function validateLineKey(value, location, diagnostics) {
  if (!requireObject(value, location, diagnostics)) return null;
  rejectUnknownKeys(value, new Set(['rootSessionId', 'topologyVersion']), location, diagnostics);
  const rootSessionId = requireString(value.rootSessionId, `${location}.rootSessionId`, diagnostics);
  const topologyVersion = requireInteger(value.topologyVersion, `${location}.topologyVersion`, diagnostics, 1);
  return rootSessionId && topologyVersion ? { rootSessionId, topologyVersion } : null;
}

function validateWorldRef(value, location, diagnostics) {
  if (!requireObject(value, location, diagnostics)) return null;
  rejectUnknownKeys(value, new Set(['projectId', 'worldId']), location, diagnostics);
  const projectId = requireString(value.projectId, `${location}.projectId`, diagnostics);
  const worldId = requireString(value.worldId, `${location}.worldId`, diagnostics);
  return projectId && worldId ? { projectId, worldId } : null;
}

function validatePosition(value, location, diagnostics) {
  if (!requireObject(value, location, diagnostics)) return null;
  rejectUnknownKeys(value, new Set(['worldVersion', 'entryIndex']), location, diagnostics);
  const worldVersion = requireInteger(value.worldVersion, `${location}.worldVersion`, diagnostics, 1);
  const entryIndex = requireInteger(value.entryIndex, `${location}.entryIndex`, diagnostics, 0);
  return worldVersion && entryIndex !== null ? { worldVersion, entryIndex } : null;
}

function validateUniqueStrings(value, location, diagnostics, { nonEmpty = false } = {}) {
  if (!requireArray(value, location, diagnostics)) return null;
  if (nonEmpty && value.length === 0) diagnostics.push(`${location} must contain at least one item`);
  const result = [];
  const seen = new Set();
  for (const [index, item] of value.entries()) {
    const parsed = requireString(item, `${location}[${index}]`, diagnostics);
    if (!parsed) continue;
    if (seen.has(parsed)) diagnostics.push(`${location}[${index}] duplicates "${parsed}"`);
    seen.add(parsed);
    result.push(parsed);
  }
  return result;
}

function rejectUnknownKeys(value, allowed, location, diagnostics) {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) diagnostics.push(`${location}.${key} is not supported by formatVersion 1`);
  }
}

function requireObject(value, location, diagnostics) {
  if (isPlainObject(value)) return true;
  diagnostics.push(`${location} must be an object`);
  return false;
}

function requireArray(value, location, diagnostics) {
  if (Array.isArray(value)) return true;
  diagnostics.push(`${location} must be an array`);
  return false;
}

function requireString(value, location, diagnostics) {
  if (typeof value === 'string' && value.trim().length > 0) return value;
  diagnostics.push(`${location} must be a non-empty string`);
  return null;
}

function requireInteger(value, location, diagnostics, minimum) {
  if (Number.isSafeInteger(value) && value >= minimum) return value;
  diagnostics.push(`${location} must be an integer >= ${minimum}`);
  return null;
}

function detectCycle(nodes, getId, getNext, message, diagnostics) {
  const state = new Map();
  const visit = (node) => {
    const id = getId(node);
    if (state.get(id) === 1) return true;
    if (state.get(id) === 2) return false;
    state.set(id, 1);
    for (const next of getNext(node)) {
      if (visit(next)) return true;
    }
    state.set(id, 2);
    return false;
  };
  if (nodes.some(visit)) diagnostics.push(message);
}

function canonicalStringify(value, space) {
  const canonical = (item) => {
    if (Array.isArray(item)) return item.map(canonical);
    if (!isPlainObject(item)) return item;
    return Object.fromEntries(Object.keys(item).sort().map((key) => [key, canonical(item[key])]));
  };
  return `${JSON.stringify(canonical(value), null, space)}\n`;
}

function isPlainObject(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function isValidTransitionPart(part) {
  return isPlainObject(part)
    && isPlainObject(part.lineKey)
    && typeof part.lineKey.rootSessionId === 'string'
    && Number.isSafeInteger(part.lineKey.topologyVersion)
    && Array.isArray(part.memberAgentIds)
    && part.memberAgentIds.every((member) => typeof member === 'string');
}

function isValidAwareDateTime(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText,
    , offsetHourText = '0', offsetMinuteText = '0'] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const offsetHour = Number(offsetHourText);
  const offsetMinute = Number(offsetMinuteText);
  if (month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59
      || offsetHour > 23 || offsetMinute > 59) return false;
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= daysInMonth[month - 1];
}

function arrayEntries(value) {
  return Array.isArray(value) ? value.entries() : [];
}

function encodeLineKey(key) {
  return `${key.rootSessionId}\0${key.topologyVersion}`;
}

function encodeLineageEdge(parentId, childId) {
  return `${parentId}\u0001${childId}`;
}

function encodePosition(position) {
  return `${position.worldVersion}:${position.entryIndex}`;
}

function comparePositions(left, right) {
  return left.worldVersion - right.worldVersion || left.entryIndex - right.entryIndex;
}

function compareText(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

function sameWorld(left, right) {
  return left.projectId === right.projectId && left.worldId === right.worldId;
}

function sameStringSet(left, right) {
  return left.length === right.length && new Set(left).size === new Set(right).size
    && left.every((item) => new Set(right).has(item));
}

function flatMembers(parts) {
  return parts.flatMap((part) => part.memberAgentIds);
}

function intersects(left, right) {
  const rightSet = new Set(right);
  return left.some((item) => rightSet.has(item));
}

function lineLabel(key) {
  return `${key.rootSessionId} @ v${key.topologyVersion}`;
}

async function statIfExists(filePath) {
  try {
    return await stat(filePath);
  } catch (error) {
    if (error?.code === 'ENOENT') return null;
    throw error;
  }
}

async function writePrivateAtomic(outputPath, content) {
  const directory = path.dirname(outputPath);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const temporaryPath = path.join(
    directory,
    `.${path.basename(outputPath)}.${process.pid}.${randomUUID()}.tmp`,
  );
  let handle;
  try {
    handle = await open(temporaryPath, 'wx', 0o600);
    await handle.writeFile(content, 'utf8');
    await handle.sync();
    await handle.close();
    handle = null;
    await rename(temporaryPath, outputPath);
  } finally {
    if (handle) await handle.close();
    await unlink(temporaryPath).catch((error) => {
      if (error?.code !== 'ENOENT') throw error;
    });
  }
}

function parseCliArgs(args) {
  if (args.includes('--help') || args.includes('-h')) return { help: true };
  const positional = [];
  let outputPath = null;
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === '--out') {
      if (outputPath !== null) throw new Error('--out may be specified only once');
      outputPath = args[index + 1] ?? null;
      if (!outputPath || outputPath.startsWith('-')) throw new Error('--out requires an output HTML path');
      index += 1;
    } else if (argument.startsWith('-')) {
      throw new Error(`Unknown option "${argument}"`);
    } else {
      positional.push(argument);
    }
  }
  if (positional.length !== 1) {
    throw new Error(CLI_USAGE);
  }
  return { help: false, inputPath: positional[0], outputPath };
}

async function resolveCliPaths({ inputPath, outputPath }) {
  const candidate = path.resolve(inputPath);
  let candidateStats;
  try {
    candidateStats = await stat(candidate);
  } catch (error) {
    throw new Error(`Cannot inspect StoryLine input ${candidate}: ${error.message}`);
  }

  const resolvedInput = candidateStats.isDirectory()
    ? path.join(candidate, 'storyline.json')
    : candidate;
  const extension = path.extname(resolvedInput);
  const defaultOutput = path.join(
    path.dirname(resolvedInput),
    `${path.basename(resolvedInput, extension)}.html`,
  );
  return {
    inputPath: resolvedInput,
    outputPath: outputPath ?? defaultOutput,
  };
}

async function main() {
  try {
    const options = parseCliArgs(process.argv.slice(2));
    if (options.help) {
      process.stdout.write(`${CLI_USAGE}\n`);
      return;
    }
    const paths = await resolveCliPaths(options);
    const result = await convertStoryLineFile(paths);
    process.stdout.write(`✓ rendered ${result.inputPath} → ${result.outputPath}\n`);
  } catch (error) {
    if (error instanceof StoryLineValidationError) {
      process.stderr.write(`${error.message}\n${error.diagnostics.map((item) => `- ${item}`).join('\n')}\n`);
    } else {
      process.stderr.write(`${error.message}\n`);
    }
    process.exitCode = 1;
  }
}

const isMain = process.argv[1]
  && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));
if (isMain) await main();
