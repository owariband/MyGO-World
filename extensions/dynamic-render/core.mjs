import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { compileStory, validateStory } from '../../tools/mygo-author-lib.mjs';

const SAFE_ID = /^[a-z][a-z0-9_-]*$/;
const ALLOWED_BEATS = new Set(['chapter', 'bgm', 'stop_bgm', 'background', 'show', 'hide', 'dialogue', 'narration']);

export class DynamicTimelineValidationError extends Error {
  constructor(diagnostics) {
    super('Dynamic timeline validation failed with ' + diagnostics.length + ' error(s)');
    this.name = 'DynamicTimelineValidationError';
    this.diagnostics = diagnostics;
  }
}

export async function loadAndCompileTimeline({ timelinePath, rootDir }) {
  let source;
  try {
    source = JSON.parse(await readFile(timelinePath, 'utf8'));
  } catch (error) {
    throw new DynamicTimelineValidationError(['Cannot read timeline ' + timelinePath + ': ' + error.message]);
  }
  return compileTimeline(source, rootDir);
}

export async function compileTimeline(source, rootDir) {
  const diagnostics = [];
  requirePositiveInteger(source?.formatVersion, 'formatVersion', diagnostics);
  requirePositiveInteger(source?.revision, 'revision', diagnostics);
  requireId(source?.activeEventId, 'activeEventId', diagnostics);
  if (!Array.isArray(source?.events) || source.events.length === 0) diagnostics.push('events must be a non-empty array');

  const eventIds = new Set();
  const renderIds = new Set();
  const compiledEvents = [];
  for (const [eventIndex, event] of entries(source?.events)) {
    const eventPath = 'events[' + eventIndex + ']';
    requireId(event?.id, eventPath + '.id', diagnostics);
    requireString(event?.title, eventPath + '.title', diagnostics);
    requireString(event?.location, eventPath + '.location', diagnostics);
    if (!Array.isArray(event?.characters) || event.characters.length === 0) diagnostics.push(eventPath + '.characters must be a non-empty array');
    if (!Array.isArray(event?.renders)) diagnostics.push(eventPath + '.renders must be an array');
    if (event?.id && eventIds.has(event.id)) diagnostics.push(eventPath + '.id duplicates event ' + event.id);
    if (event?.id) eventIds.add(event.id);

    const compiledRenders = [];
    for (const [renderIndex, render] of entries(event?.renders)) {
      const renderPath = eventPath + '.renders[' + renderIndex + ']';
      requireId(render?.id, renderPath + '.id', diagnostics);
      requireString(render?.title, renderPath + '.title', diagnostics);
      if (!Array.isArray(render?.beats) || render.beats.length === 0) {
        diagnostics.push(renderPath + '.beats must be a non-empty array');
        continue;
      }
      if (render?.estimatedPlayMs !== undefined) requirePositiveInteger(render.estimatedPlayMs, renderPath + '.estimatedPlayMs', diagnostics);
      if (render?.id && renderIds.has(render.id)) diagnostics.push(renderPath + '.id duplicates render ' + render.id);
      if (render?.id) renderIds.add(render.id);

      let hasBackground = false;
      let hasVisibleText = false;
      for (const [beatIndex, beat] of render.beats.entries()) {
        const beatPath = renderPath + '.beats[' + beatIndex + ']';
        if (!ALLOWED_BEATS.has(beat?.type)) diagnostics.push(beatPath + '.type ' + String(beat?.type) + ' is not supported by dynamic renders');
        hasBackground ||= beat?.type === 'background';
        hasVisibleText ||= beat?.type === 'dialogue' || beat?.type === 'narration';
      }
      if (!hasBackground) diagnostics.push(renderPath + '.beats must establish a background');
      if (!hasVisibleText) diagnostics.push(renderPath + '.beats must contain dialogue or narration');

      if (SAFE_ID.test(event?.id ?? '') && SAFE_ID.test(render?.id ?? '')) {
        const result = await compileRender(event, render, rootDir);
        diagnostics.push(...result.diagnostics.map((item) => renderPath + ': ' + item));
        if (result.render) compiledRenders.push(result.render);
      }
    }
    if (event?.id) compiledEvents.push({ id: event.id, title: event.title, location: event.location, characters: event.characters, renders: compiledRenders });
  }

  if (source?.activeEventId && !eventIds.has(source.activeEventId)) diagnostics.push('activeEventId references unknown event ' + source.activeEventId);
  if (diagnostics.length > 0) throw new DynamicTimelineValidationError(diagnostics);
  return { formatVersion: source.formatVersion, revision: source.revision, activeEventId: source.activeEventId, events: compiledEvents };
}

export function materializeRenderScript(render, dispatchId) {
  return [
    'setVar:__dynamic_render_active=' + dispatchId + ' -next;',
    render.script.trim(),
    'setVar:__dynamic_render_done=' + dispatchId + ';',
    '',
  ].join('\n');
}

export class DynamicTimelineRuntime {
  constructor(timeline) {
    this.timeline = timeline;
    this.selectedEventId = timeline.activeEventId;
    this.played = new Set();
    this.active = null;
    this.rendererConnected = false;
  }

  replaceTimeline(timeline) {
    const valid = new Set(timeline.events.flatMap((event) => event.renders.map((render) => renderKey(event.id, render))));
    const previousActive = this.active;
    const missingCommitted = [...this.played].find((key) => !valid.has(key));
    if (missingCommitted) throw new Error('Timeline cannot remove or rewrite played render ' + missingCommitted);
    if (previousActive && !valid.has(renderKey(previousActive.eventId, previousActive.render))) {
      throw new Error('Timeline cannot remove or rewrite active render ' + previousActive.render.id);
    }
    this.timeline = timeline;
    if (!timeline.events.some((event) => event.id === this.selectedEventId)) this.selectedEventId = timeline.activeEventId;
    if (previousActive) {
      const event = timeline.events.find((item) => item.id === previousActive.eventId);
      const render = event?.renders.find((item) => renderKey(event.id, item) === renderKey(previousActive.eventId, previousActive.render));
      this.active = render ? { ...previousActive, render } : null;
    }
  }

  setRendererConnected(connected) {
    this.rendererConnected = connected;
    if (!connected) this.active = null;
  }

  selectEvent(eventId) {
    if (!this.timeline.events.some((event) => event.id === eventId)) throw new Error('Unknown dynamic event ' + eventId);
    if (eventId !== this.selectedEventId) {
      this.selectedEventId = eventId;
      this.active = null;
    }
  }

  beginNext(dispatchId) {
    if (this.active) return null;
    const event = this.timeline.events.find((item) => item.id === this.selectedEventId);
    const render = event?.renders.find((item) => !this.played.has(renderKey(event.id, item)));
    if (!event || !render) return null;
    this.active = { dispatchId, eventId: event.id, render, status: 'loading' };
    return this.active;
  }

  markPlaying(dispatchId) {
    if (!this.active || this.active.dispatchId !== dispatchId) return false;
    this.active.status = 'playing';
    return true;
  }

  complete(dispatchId) {
    if (!this.active || this.active.dispatchId !== dispatchId) return false;
    this.played.add(renderKey(this.active.eventId, this.active.render));
    this.active = null;
    return true;
  }

  cancelActive() { this.active = null; }

  snapshot() {
    return {
      type: 'timeline.snapshot',
      revision: this.timeline.revision,
      selectedEventId: this.selectedEventId,
      playback: {
        mode: this.rendererConnected && this.active ? this.active.status : 'black',
        rendererConnected: this.rendererConnected,
        eventId: this.active?.eventId ?? null,
        renderId: this.active?.render.id ?? null,
      },
      events: this.timeline.events.map((event) => {
        const renders = event.renders.map((render) => {
          const active = this.active?.eventId === event.id && this.active.render.id === render.id;
          const played = this.played.has(renderKey(event.id, render));
          return { id: render.id, title: render.title, estimatedPlayMs: render.estimatedPlayMs, status: active ? this.active.status : played ? 'played' : 'ready' };
        });
        const readyCount = renders.filter((render) => render.status === 'ready').length;
        return {
          id: event.id,
          title: event.title,
          location: event.location,
          selected: event.id === this.selectedEventId,
          status: this.active?.eventId === event.id ? 'live' : readyCount > 0 ? 'ready' : 'complete',
          readyCount,
          playedCount: renders.filter((render) => render.status === 'played').length,
          renders,
        };
      }),
    };
  }
}

async function compileRender(event, render, rootDir) {
  const boundaryText = '__DYNAMIC_RENDER_BOUNDARY_' + render.id + '__';
  const used = new Set(render.beats.map((beat) => beat?.id).filter(Boolean));
  const boundaryId = uniqueId(render.id + '_boundary', used);
  used.add(boundaryId);
  const endingId = uniqueId(render.id + '_ending', used);
  const story = {
    formatVersion: 1,
    title: render.title,
    entryScene: render.id,
    characters: event.characters,
    scenes: [{
      id: render.id,
      title: render.title,
      beats: [...render.beats, { id: boundaryId, type: 'narration', text: boundaryText }, { id: endingId, type: 'ending', title: 'Dynamic render boundary' }],
    }],
  };
  const diagnostics = await validateStory(story, rootDir);
  if (diagnostics.length > 0) return { diagnostics, render: null };
  const lines = compileStory(story, 'dynamic-render:' + event.id + '/' + render.id).split('\n');
  const start = lines.indexOf('label:' + render.id + ';') + 1;
  const end = lines.indexOf(':' + boundaryText + ';', start);
  if (start === 0 || end < start) return { diagnostics: ['compiled render boundary was not found'], render: null };
  const script = lines.slice(start, end).join('\n').trim();
  return { diagnostics: [], render: { id: render.id, title: render.title, estimatedPlayMs: render.estimatedPlayMs ?? estimatePlayMs(render.beats), contentHash: createHash('sha256').update(script).digest('hex'), script } };
}

function estimatePlayMs(beats) {
  return beats.reduce((total, beat) => (beat.type === 'dialogue' || beat.type === 'narration') ? total + Math.max(2500, String(beat.text ?? '').length * 80) : total + 400, 0);
}

function renderKey(eventId, render) { return eventId + ':' + render.id + ':' + render.contentHash; }

function uniqueId(base, used) {
  let candidate = base;
  let suffix = 2;
  while (used.has(candidate)) candidate = base + '_' + suffix++;
  return candidate;
}

function requireString(value, path, diagnostics) {
  if (typeof value !== 'string' || value.trim().length === 0) diagnostics.push(path + ' must be a non-empty string');
}

function requireId(value, path, diagnostics) {
  requireString(value, path, diagnostics);
  if (typeof value === 'string' && !SAFE_ID.test(value)) diagnostics.push(path + ' must match ' + SAFE_ID);
}

function requirePositiveInteger(value, path, diagnostics) {
  if (!Number.isInteger(value) || value < 1) diagnostics.push(path + ' must be a positive integer');
}

function entries(value) { return Array.isArray(value) ? value.entries() : []; }
