const LINE_COLORS = [
  '#59a8ff',
  '#e6a14f',
  '#a88bff',
  '#e56887',
  '#63bd7d',
  '#68c4c8',
  '#d9c75f',
  '#ca83df',
];

const ENTRY_KIND_LABELS = {
  dialogue: '对话',
  action: '动作',
  behavior: '行为',
  session_transition: '拓扑变化',
};

const GRAPH_LEFT = 220;
const EVENT_GAP = 350;
const CURVE_SPAN = 86;
const GRAPH_TOP = 130;
const LANE_GAP = 390;

export function renderWorktreeDocument({ value, inspection, canonicalJson, artifactHash, agentLabels = {} }) {
  const model = buildWorktreeModel(value, inspection);
  const worldLabel = `${value.worldRef.projectId} / ${value.worldRef.worldId}`;
  const builtThrough = value.builtThrough
    ? `${value.builtThrough.worldVersion}:${value.builtThrough.entryIndex}`
    : 'no committed entries';
  const agentIds = [...inspection.agentIds].sort(compareText);
  const kinds = [...inspection.entryKinds].sort(compareText);

  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
  <title>${escapeHtml(worldLabel)} · StoryLine worktree</title>
  <style>
    :root {
      --page: #141618;
      --canvas: #181b1e;
      --panel: #1e2226;
      --panel-strong: #22272c;
      --ink: #edf0f3;
      --muted: #949ba4;
      --faint: #68717b;
      --rule: #343a40;
      --rule-soft: #272c31;
      --blue: #59a8ff;
      --orange: #ef9b62;
      --green: #63bd7d;
      --rose: #e56887;
      --purple: #a88bff;
      --shadow: 0 14px 32px rgba(0, 0, 0, .28);
      --body-font: "Avenir Next", "Noto Sans SC", "PingFang SC", sans-serif;
      --mono-font: "SFMono-Regular", "JetBrains Mono", Menlo, monospace;
      color: var(--ink);
      background: var(--page);
      font-family: var(--body-font);
      font-synthesis: none;
    }
    * { box-sizing: border-box; }
    html, body { min-width: 320px; min-height: 100%; margin: 0; }
    body { background: var(--page); font-size: 14px; line-height: 1.55; overflow-x: auto; }
    button, select { color: inherit; font: inherit; }
    button:focus-visible, select:focus-visible, summary:focus-visible {
      outline: 3px solid rgba(89, 168, 255, .45);
      outline-offset: 2px;
    }
    .masthead {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 2rem;
      align-items: end;
      padding: 1.5rem clamp(1rem, 3vw, 2.6rem) 1.35rem;
      background: #121416;
      border-bottom: 1px solid var(--rule);
    }
    .eyebrow, .mono, label, .event-kind, .event-position, .session-label, .hash {
      font-family: var(--mono-font);
      font-variant-numeric: tabular-nums slashed-zero;
    }
    .eyebrow {
      margin: 0 0 .45rem;
      color: var(--blue);
      font-size: .68rem;
      font-weight: 750;
      letter-spacing: .13em;
      text-transform: uppercase;
    }
    h1 {
      margin: 0;
      font-size: clamp(1.9rem, 4vw, 3.7rem);
      font-weight: 680;
      line-height: 1.02;
      letter-spacing: -.035em;
    }
    .lede { max-width: 72rem; margin: .65rem 0 0; color: var(--muted); }
    .world-meta {
      display: grid;
      grid-template-columns: auto minmax(150px, auto);
      gap: .3rem 1rem;
      margin: 0;
      font: .72rem/1.5 var(--mono-font);
    }
    .world-meta dt { color: var(--faint); text-align: right; text-transform: uppercase; }
    .world-meta dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
    main { padding: 1rem clamp(.75rem, 2vw, 1.8rem) 2rem; }
    .controls {
      position: sticky;
      z-index: 30;
      top: 0;
      display: flex;
      flex-wrap: wrap;
      gap: .55rem;
      align-items: end;
      margin-bottom: .85rem;
      padding: .75rem;
      background: rgba(20, 22, 24, .96);
      border: 1px solid var(--rule);
      box-shadow: 0 8px 24px rgba(0, 0, 0, .2);
      backdrop-filter: blur(12px);
    }
    label { display: grid; min-width: 150px; gap: .25rem; color: var(--muted); font-size: .62rem; letter-spacing: .07em; text-transform: uppercase; }
    select, .control-button {
      min-height: 2.4rem;
      padding: .5rem .7rem;
      color: var(--ink);
      background: var(--panel);
      border: 1px solid var(--rule);
      border-radius: 6px;
    }
    .control-button { min-width: 2.7rem; cursor: pointer; }
    .control-button:hover { border-color: var(--blue); }
    .control-button:disabled { color: var(--faint); cursor: default; opacity: .55; }
    .result-count { margin-left: auto; padding: .58rem .25rem; color: var(--muted); font: .7rem/1.4 var(--mono-font); white-space: nowrap; }
    .graph-heading {
      display: flex;
      flex-wrap: wrap;
      gap: .75rem 1.4rem;
      align-items: baseline;
      padding: .7rem .15rem;
    }
    .graph-heading h2 { margin: 0; font-size: 1.15rem; }
    .graph-heading p { margin: 0; color: var(--muted); font-size: .78rem; }
    .legend { display: flex; flex-wrap: wrap; gap: .45rem 1rem; margin-left: auto; }
    .legend-item { display: inline-flex; gap: .42rem; align-items: center; color: var(--muted); font: .68rem/1.4 var(--mono-font); }
    .legend-swatch { width: .58rem; height: .58rem; background: var(--line-color); border-radius: 50%; box-shadow: 0 0 0 2px rgba(255,255,255,.06); }
    .worktree-panel { background: var(--canvas); }
    .graph-scroll {
      position: relative;
      width: 100%;
      height: auto;
      min-height: 0;
      overflow: visible;
    }
    .graph-stage { position: relative; width: var(--graph-width); height: var(--graph-height); isolation: isolate; }
    .topology-svg { position: absolute; z-index: 0; inset: 0; width: 100%; height: 100%; overflow: visible; }
    .lane-guide { stroke: #242a2f; stroke-width: 1; }
    .session-segment { stroke-linecap: round; stroke-width: 4; opacity: .95; }
    .transition-edge { fill: none; stroke-linecap: round; opacity: .85; }
    .transition-junction { fill: var(--canvas); stroke-width: 3; }
    .session-label {
      position: absolute;
      z-index: 1;
      max-width: 320px;
      padding: .25rem .5rem;
      color: var(--line-color);
      background: rgba(24, 27, 30, .9);
      border: 1px solid var(--rule-soft);
      border-radius: 4px;
      font-size: .7rem;
      line-height: 1.35;
      white-space: nowrap;
      transform: translate(.45rem, -2rem);
    }
    .session-label span { color: var(--muted); }
    .timeline-event { position: absolute; z-index: 4; width: 0; height: 0; --kind-color: var(--blue); }
    .timeline-event[hidden] { display: none; }
    .timeline-event[data-kind="action"] { --kind-color: var(--green); }
    .timeline-event[data-kind="behavior"] { --kind-color: var(--rose); }
    .timeline-event[data-kind="session_transition"] { --kind-color: var(--orange); }
    .event-node {
      position: absolute;
      z-index: 3;
      left: -16px;
      top: -16px;
      width: 32px;
      height: 32px;
      padding: 0;
      background: transparent;
      border: 0;
      border-radius: 50%;
      cursor: pointer;
    }
    .event-node::before {
      position: absolute;
      inset: 6px;
      content: "";
      background: var(--kind-color);
      border: 4px solid var(--canvas);
      border-radius: 50%;
      box-shadow: 0 0 0 3px var(--line-color);
    }
    .timeline-event[data-kind="session_transition"] .event-node::before { inset: 4px; border-width: 3px; }
    .timeline-event.is-selected .event-node::before { box-shadow: 0 0 0 3px var(--line-color), 0 0 0 8px rgba(255,255,255,.12); }
    .event-card {
      position: absolute;
      left: -160px;
      top: 29px;
      width: 320px;
      padding: .9rem 1rem 1rem;
      color: var(--ink);
      background: rgba(31, 35, 39, .97);
      border: 1px solid var(--rule);
      border-top: 3px solid var(--line-color);
      border-radius: 7px;
      box-shadow: 0 10px 25px rgba(0, 0, 0, .22);
    }
    .timeline-event.is-selected .event-card { border-color: var(--line-color); }
    .event-card-top { display: flex; gap: .6rem; align-items: start; justify-content: space-between; }
    .event-kind { color: var(--kind-color); font-size: .72rem; font-weight: 780; letter-spacing: .08em; }
    .event-position { color: var(--faint); font-size: .68rem; white-space: nowrap; }
    .event-sentence { display: flex; flex-wrap: wrap; gap: .35rem; align-items: baseline; margin: .65rem 0 .55rem; font-size: .94rem; line-height: 1.45; }
    .event-sentence strong { color: #fff; font-size: 1.02rem; }
    .event-sentence span { color: #b9c0c7; }
    .agent-id { color: var(--faint); font: .62rem/1.3 var(--mono-font); }
    .event-copy {
      margin: 0;
      padding: .65rem .7rem;
      color: #f2f4f6;
      background: #181b1e;
      border-left: 2px solid var(--kind-color);
      font-size: .94rem;
      line-height: 1.68;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    blockquote.event-copy { quotes: none; }
    .event-context { margin: .65rem 0 0; color: #c4c9ce; font-size: .76rem; line-height: 1.5; overflow-wrap: anywhere; }
    .event-context strong { color: var(--ink); }
    .event-links { margin: .5rem 0 0; color: #9fb8cc; font: .66rem/1.5 var(--mono-font); overflow-wrap: anywhere; }
    .topology-change { display: grid; gap: .4rem; margin-top: .65rem; padding: .65rem; background: #28231d; border: 1px solid #5a4934; border-radius: 4px; }
    .topology-change strong { color: #f0ae72; font: .7rem/1.4 var(--mono-font); text-transform: uppercase; }
    .topology-part { color: #d7c8b8; font-size: .74rem; line-height: 1.5; overflow-wrap: anywhere; }
    .topology-part b { color: #f0ae72; }
    .technical-details { margin-top: .65rem; color: var(--faint); border-top: 1px solid var(--rule-soft); }
    .technical-details summary { padding-top: .55rem; font: .64rem/1.45 var(--mono-font); cursor: pointer; }
    .technical-details dl { display: grid; gap: .28rem; margin: .55rem 0 0; font: .62rem/1.5 var(--mono-font); }
    .technical-details div { display: grid; grid-template-columns: 5.2rem minmax(0, 1fr); gap: .45rem; }
    .technical-details dt { color: #727b84; }
    .technical-details dd { margin: 0; color: #9da5ad; overflow-wrap: anywhere; }
    .empty-result { display: none; margin: 0; padding: 1.4rem; color: var(--muted); border-top: 1px dashed var(--rule); text-align: center; }
    .empty-result[data-visible="true"] { display: block; }
    .artifact-footer { display: grid; gap: .8rem; margin-top: 1rem; }
    .hash { margin: 0; color: var(--faint); font-size: .6rem; overflow-wrap: anywhere; }
    .raw-panel { background: #111315; border: 1px solid var(--rule); }
    .raw-panel summary { padding: .8rem 1rem; color: var(--muted); cursor: pointer; }
    .raw-panel pre { max-height: 70vh; margin: 0; padding: 1rem; overflow: auto; color: #dfe3e7; border-top: 1px solid var(--rule); font: .7rem/1.55 var(--mono-font); }
    @media (max-width: 800px) {
      .masthead { grid-template-columns: 1fr; }
      .world-meta dt { text-align: left; }
      .controls { position: static; }
      .result-count { width: 100%; margin-left: 0; }
      .legend { width: 100%; margin-left: 0; }
      .graph-scroll { height: auto; }
    }
    @media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
  </style>
</head>
<body>
  <header class="masthead">
    <div>
      <p class="eyebrow">World interaction worktree · committed events</p>
      <h1>每一个点，都是一次真实发生</h1>
      <p class="lede">从左向右阅读：线汇入代表 join / merge，线分叉代表 leave / split。每个节点旁直接展示本次对话、动作、行为或拓扑变化的完整正文。</p>
    </div>
    <dl class="world-meta">
      <dt>Project</dt><dd>${escapeHtml(value.worldRef.projectId)}</dd>
      <dt>World</dt><dd>${escapeHtml(value.worldRef.worldId)}</dd>
      <dt>Built through</dt><dd>${escapeHtml(builtThrough)}</dd>
      <dt>Status</dt><dd>${escapeHtml(value.status)}</dd>
    </dl>
  </header>
  <main>
    <section class="controls" aria-label="StoryLine filters and navigation">
      ${renderSelect('agent-filter', '角色', '全部角色', agentIds.map((id) => [id, agentOptionLabel(id, agentLabels)]))}
      ${renderSelect('line-filter', '互动线', '全部互动线', model.roots.map((root) => [root.id, sessionDisplayName(root.id, agentLabels)]))}
      ${renderSelect('kind-filter', 'Event kind', 'All events', kinds.map((kind) => [kind, `${ENTRY_KIND_LABELS[kind] ?? kind} · ${kind}`]))}
      <button class="control-button" id="reset-filters" type="button">重置</button>
      <button class="control-button" id="previous-event" type="button" aria-label="上一个可见事件">← 上一个</button>
      <button class="control-button" id="next-event" type="button" aria-label="下一个可见事件">下一个 →</button>
      <button class="control-button" id="next-transition" type="button">下个拓扑变化</button>
      <output class="result-count" id="result-count" aria-live="polite"></output>
    </section>

    <section class="worktree-panel" aria-labelledby="graph-title">
      <div class="graph-heading">
        <h2 id="graph-title">Interactive worktree</h2>
        <p>${model.events.length} 个事件 · ${inspection.lines.length} 个 session 片段 · 时间向右</p>
        <div class="legend" aria-label="Session line colors">${renderLegend(model.roots, agentLabels)}</div>
      </div>
      <div class="graph-scroll" id="graph-scroll" tabindex="0" aria-label="横向 StoryLine 事件图">
        <div class="graph-stage" style="--graph-width:${model.width}px;--graph-height:${model.height}px">
          ${renderTopology(model)}
          ${model.segments.map((segment) => renderSessionLabel(segment, agentLabels)).join('')}
          ${model.events.map((event, index) => renderEvent(event, index, model.entryPositionById, agentLabels)).join('')}
        </div>
      </div>
      <p class="empty-result" id="empty-result">没有符合当前筛选条件的 committed event。</p>
    </section>

    <footer class="artifact-footer">
      <p class="hash">Canonical JSON SHA-256 · ${artifactHash}</p>
      <details class="raw-panel">
        <summary>Show raw canonical JSON</summary>
        <pre>${escapeHtml(canonicalJson)}</pre>
      </details>
    </footer>
  </main>
  <script>
    (() => {
      const agent = document.querySelector('#agent-filter');
      const line = document.querySelector('#line-filter');
      const kind = document.querySelector('#kind-filter');
      const count = document.querySelector('#result-count');
      const empty = document.querySelector('#empty-result');
      const events = [...document.querySelectorAll('.timeline-event')];
      const previous = document.querySelector('#previous-event');
      const next = document.querySelector('#next-event');
      const nextTransition = document.querySelector('#next-transition');
      const motion = globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
      let selected = null;

      function visibleEvents() {
        return events.filter((event) => !event.hidden);
      }

      function selectEvent(event, scroll) {
        if (!event || event.hidden) return;
        for (const candidate of events) candidate.classList.toggle('is-selected', candidate === event);
        selected = event;
        if (scroll) {
          event.querySelector('.event-card').scrollIntoView({
            behavior: motion,
            block: 'center',
            inline: 'center',
          });
        }
      }

      function applyFilters() {
        const visibleRoots = new Set();
        let visibleCount = 0;
        for (const event of events) {
          const agents = JSON.parse(event.dataset.agents);
          const matches = (!agent.value || agents.includes(agent.value))
            && (!line.value || event.dataset.line === line.value)
            && (!kind.value || event.dataset.kind === kind.value);
          event.hidden = !matches;
          if (matches) {
            visibleCount += 1;
            visibleRoots.add(event.dataset.line);
          }
        }
        count.value = visibleCount + ' events · ' + visibleRoots.size + ' sessions';
        empty.dataset.visible = String(visibleCount === 0);
        previous.disabled = visibleCount === 0;
        next.disabled = visibleCount === 0;
        nextTransition.disabled = !visibleEvents().some((event) => event.dataset.kind === 'session_transition');
        if (!selected || selected.hidden) selectEvent(visibleEvents()[0], false);
      }

      function move(step) {
        const visible = visibleEvents();
        if (!visible.length) return;
        const current = Math.max(0, visible.indexOf(selected));
        selectEvent(visible[Math.max(0, Math.min(visible.length - 1, current + step))], true);
      }

      for (const event of events) {
        event.querySelector('.event-node').addEventListener('click', () => selectEvent(event, true));
      }
      for (const select of [agent, line, kind]) select.addEventListener('change', applyFilters);
      document.querySelector('#reset-filters').addEventListener('click', () => {
        agent.value = '';
        line.value = '';
        kind.value = '';
        applyFilters();
      });
      previous.addEventListener('click', () => move(-1));
      next.addEventListener('click', () => move(1));
      nextTransition.addEventListener('click', () => {
        const visible = visibleEvents();
        const current = visible.indexOf(selected);
        const transition = visible.slice(current + 1).find((event) => event.dataset.kind === 'session_transition')
          ?? visible.find((event) => event.dataset.kind === 'session_transition');
        selectEvent(transition, true);
      });
      agent.value = '';
      line.value = '';
      kind.value = '';
      applyFilters();
      selectEvent(visibleEvents()[0], false);
    })();
  </script>
</body>
</html>
`;
}

function buildWorktreeModel(value, inspection) {
  const latestVersion = value.builtThrough?.worldVersion
    ?? Math.max(1, ...inspection.entries.map((record) => record.position.worldVersion));
  const rangesByRoot = new Map();

  for (const record of inspection.lines) {
    const root = record.key.rootSessionId;
    const end = record.childKeys.length
      ? Math.max(...record.childKeys.map((key) => key.topologyVersion))
      : latestVersion + 1;
    const current = rangesByRoot.get(root);
    rangesByRoot.set(root, {
      id: root,
      start: Math.min(current?.start ?? record.key.topologyVersion, record.key.topologyVersion),
      end: Math.max(current?.end ?? end, end),
      firstLineIndex: Math.min(current?.firstLineIndex ?? record.lineIndex, record.lineIndex),
    });
  }

  const roots = [...rangesByRoot.values()].sort((left, right) => (
    left.start - right.start
    || left.firstLineIndex - right.firstLineIndex
    || compareText(left.id, right.id)
  ));
  const laneEnds = [];
  for (const root of roots) {
    let lane = laneEnds.findIndex((end) => end <= root.start);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(root.end);
    } else {
      laneEnds[lane] = root.end;
    }
    root.lane = lane;
    root.y = GRAPH_TOP + lane * LANE_GAP;
    root.color = LINE_COLORS[lane % LINE_COLORS.length];
  }
  const rootById = new Map(roots.map((root) => [root.id, root]));

  const entries = [...inspection.entries].sort((left, right) => (
    comparePositions(left.position, right.position)
    || compareText(left.entry.entryId, right.entry.entryId)
  ));
  const events = entries.map((record, index) => {
    const root = rootById.get(record.entry.rootSessionIdAtCommit);
    return {
      record,
      x: GRAPH_LEFT + index * EVENT_GAP,
      y: root.y,
      root,
    };
  });
  const entryPositionById = new Map(events.map(({ record }) => [
    record.entry.entryId,
    positionLabel(record.position),
  ]));
  const startXByLineKey = new Map();
  for (const event of events) {
    if (!event.record.entry.transition) continue;
    for (const part of event.record.entry.transition.afterParts) {
      startXByLineKey.set(encodeLineKey(part.lineKey), event.x);
    }
  }

  const width = Math.max(1100, (events.at(-1)?.x ?? GRAPH_LEFT) + 190);
  const height = GRAPH_TOP + Math.max(laneEnds.length, 1) * LANE_GAP + 90;
  const segments = inspection.lines.map((record) => {
    const root = rootById.get(record.key.rootSessionId);
    const childStarts = record.childKeys
      .map((key) => startXByLineKey.get(encodeLineKey(key)))
      .filter((x) => x !== undefined);
    const startX = record.parentKeys.length
      ? startXByLineKey.get(record.keyId) ?? GRAPH_LEFT
      : 76;
    const proposedEnd = childStarts.length ? Math.min(...childStarts) - CURVE_SPAN : width - 70;
    return {
      record,
      root,
      startX,
      endX: Math.max(startX, proposedEnd),
    };
  });

  const connectors = [];
  const junctions = [];
  for (const event of events) {
    const transition = event.record.entry.transition;
    if (!transition) continue;
    for (const before of transition.beforeParts) {
      for (const after of transition.afterParts) {
        const shared = overlapCount(before.memberAgentIds, after.memberAgentIds);
        if (shared === 0) continue;
        const beforeRoot = rootById.get(before.lineKey.rootSessionId);
        const afterRoot = rootById.get(after.lineKey.rootSessionId);
        connectors.push({
          x1: event.x - CURVE_SPAN,
          y1: beforeRoot.y,
          x2: event.x,
          y2: afterRoot.y,
          color: afterRoot.color,
          width: 2.4 + Math.min(shared, 4) * .7,
        });
      }
    }
    for (const after of transition.afterParts) {
      const root = rootById.get(after.lineKey.rootSessionId);
      junctions.push({ x: event.x, y: root.y, color: root.color });
    }
  }

  return { roots, events, entryPositionById, width, height, segments, connectors, junctions, laneCount: laneEnds.length };
}

function renderTopology(model) {
  const guides = Array.from({ length: Math.max(model.laneCount, 1) }, (_, lane) => {
    const y = GRAPH_TOP + lane * LANE_GAP;
    return `<line class="lane-guide" x1="48" y1="${y}" x2="${model.width - 48}" y2="${y}"></line>`;
  }).join('');
  const segments = model.segments.map(({ root, startX, endX }) => (
    `<line class="session-segment" x1="${startX}" y1="${root.y}" x2="${endX}" y2="${root.y}" stroke="${root.color}"></line>`
  )).join('');
  const connectors = model.connectors.map((edge) => {
    const bend = CURVE_SPAN * .46;
    const path = `M ${edge.x1} ${edge.y1} C ${edge.x1 + bend} ${edge.y1} ${edge.x2 - bend} ${edge.y2} ${edge.x2} ${edge.y2}`;
    return `<path class="transition-edge" d="${path}" stroke="${edge.color}" stroke-width="${edge.width}"></path>`;
  }).join('');
  const junctions = model.junctions.map((junction) => (
    `<circle class="transition-junction" cx="${junction.x}" cy="${junction.y}" r="6" stroke="${junction.color}"></circle>`
  )).join('');
  return `<svg class="topology-svg" viewBox="0 0 ${model.width} ${model.height}" aria-hidden="true">${guides}${segments}${connectors}${junctions}</svg>`;
}

function renderSessionLabel(segment, agentLabels) {
  const members = segment.record.members.map((id) => displayAgent(id, agentLabels)).join(' · ');
  const label = `${sessionDisplayName(segment.record.key.rootSessionId, agentLabels)} @ v${segment.record.key.topologyVersion}`;
  return `<div class="session-label" style="left:${segment.startX}px;top:${segment.root.y}px;--line-color:${segment.root.color}">${escapeHtml(label)} <span>${escapeHtml(members)}</span></div>`;
}

function renderEvent(event, index, entryPositionById, agentLabels) {
  const entry = event.record.entry;
  const agents = [...new Set([entry.actorAgentId, ...entry.recipientAgentIds])];
  const presentation = eventPresentation(entry, agentLabels);
  const participants = entry.recipientAgentIds.map((id) => displayAgent(id, agentLabels)).join('、');
  const links = entry.links.length
    ? `<p class="event-links">关联 · ${entry.links.map((link) => `${escapeHtml(link.relationKind)} → ${escapeHtml(entryPositionById.get(link.relatedEntryId) ?? link.relatedEntryId)}`).join(' · ')}</p>`
    : '';
  const transition = entry.transition ? renderTransition(entry.transition, agentLabels) : '';
  const supplementalSections = [links, transition].filter(Boolean).join('\n      ');
  const copyTag = entry.entryKind === 'dialogue' ? 'blockquote' : 'p';
  const cardId = `event-card-${index}`;
  const label = ENTRY_KIND_LABELS[entry.entryKind] ?? entry.entryKind;
  return `<div class="timeline-event" data-kind="${escapeAttribute(entry.entryKind)}" data-line="${escapeAttribute(entry.rootSessionIdAtCommit)}" data-agents="${escapeAttribute(JSON.stringify(agents))}" style="left:${event.x}px;top:${event.y}px;--line-color:${event.root.color}">
    <button class="event-node" type="button" aria-label="${escapeAttribute(`${label}：${presentation.lead}。${presentation.copy}`)}" aria-describedby="${cardId}"></button>
    <article class="event-card" id="${cardId}">
      <div class="event-card-top"><span class="event-kind">${escapeHtml(label)}</span><span class="event-position">${escapeHtml(positionLabel(entry.commitPosition))}</span></div>
      <p class="event-sentence">${escapeHtml(presentation.lead)}</p>
      <${copyTag} class="event-copy">${escapeHtml(presentation.copy)}</${copyTag}>
      <p class="event-context"><strong>同场角色：</strong>${escapeHtml(participants)}</p>
      ${supplementalSections ? `${supplementalSections}\n      ` : ''}${renderTechnicalDetails(entry)}
    </article>
  </div>`;
}

function eventPresentation(entry, agentLabels) {
  const actor = displayAgent(entry.actorAgentId, agentLabels);
  const target = entry.targetAgentId ? displayAgent(entry.targetAgentId, agentLabels) : null;
  if (entry.entryKind === 'dialogue') {
    return { lead: `${actor} 对 ${target} 说`, copy: `「${entry.text}」` };
  }
  if (entry.entryKind === 'action') {
    return { lead: `${actor} 做了一个动作`, copy: entry.text };
  }
  if (entry.entryKind === 'behavior') {
    return { lead: `${actor} 做了一个可观察行为`, copy: entry.text };
  }

  const reason = entry.transition.reason;
  if (reason === 'split') {
    return { lead: `${actor} 离开了当前互动`, copy: `${actor} 离开后，原互动线分成新的分支。` };
  }
  if (reason === 'merge') {
    return { lead: `${actor} 让两条互动线合流`, copy: `${actor} 加入 ${target} 所在互动，两组成员成为同一条互动线。` };
  }
  return { lead: `${actor} 改变了互动归属`, copy: `${actor} 离开原互动，加入 ${target} 所在互动。` };
}

function renderTechnicalDetails(entry) {
  const optional = [
    ['Target agent ID', entry.targetAgentId],
    ['Target object ID', entry.targetObjectId],
    ['Operation', entry.operationId],
  ].filter(([, value]) => value !== undefined);
  return `<details class="technical-details">
    <summary>技术信息 · IDs / session / time</summary>
    <dl>
      <div><dt>Entry ID</dt><dd>${escapeHtml(entry.entryId)}</dd></div>
      <div><dt>Kind</dt><dd>${escapeHtml(entry.entryKind)}</dd></div>
      <div><dt>Actor ID</dt><dd>${escapeHtml(entry.actorAgentId)}</dd></div>
      <div><dt>Session</dt><dd>${escapeHtml(lineLabel({ rootSessionId: entry.rootSessionIdAtCommit, topologyVersion: entry.topologyVersion }))}</dd></div>
      <div><dt>Delivery</dt><dd>${escapeHtml(entry.deliveryChannel)}</dd></div>
      <div><dt>Occurred</dt><dd>${escapeHtml(entry.occurredAt)}</dd></div>
${optional.map(([label, value]) => `      <div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join('\n')}
    </dl>
  </details>`;
}

function renderTransition(transition, agentLabels) {
  return `<section class="topology-change" aria-label="${escapeAttribute(transition.reason)} transition">
    <strong>${escapeHtml(transition.reason)} · membership change</strong>
    <div class="topology-part"><b>变化前</b> · ${transition.beforeParts.map((part) => renderTransitionPart(part, agentLabels)).join(' / ')}</div>
    <div class="topology-part"><b>变化后</b> · ${transition.afterParts.map((part) => renderTransitionPart(part, agentLabels)).join(' / ')}</div>
  </section>`;
}

function renderTransitionPart(part, agentLabels) {
  const line = `${sessionDisplayName(part.lineKey.rootSessionId, agentLabels)} @ v${part.lineKey.topologyVersion}`;
  const members = part.memberAgentIds.map((id) => displayAgent(id, agentLabels)).join('、');
  return `${escapeHtml(line)} [${escapeHtml(members)}]`;
}

function renderLegend(roots, agentLabels) {
  return roots.map((root) => (
    `<span class="legend-item"><i class="legend-swatch" style="--line-color:${root.color}"></i>${escapeHtml(sessionDisplayName(root.id, agentLabels))}</span>`
  )).join('');
}

function renderSelect(id, label, emptyLabel, options) {
  return `<label for="${id}">${label}<select id="${id}"><option value="">${emptyLabel}</option>${options
    .map(([value, text]) => `<option value="${escapeAttribute(value)}">${escapeHtml(text)}</option>`)
    .join('')}</select></label>`;
}

function agentOptionLabel(id, agentLabels) {
  const displayName = displayAgent(id, agentLabels);
  return displayName === id ? id : `${displayName}（${id}）`;
}

function displayAgent(id, agentLabels) {
  return Object.hasOwn(agentLabels, id) ? agentLabels[id] : id;
}

function sessionDisplayName(rootSessionId, agentLabels) {
  const ownerId = rootSessionId.startsWith('session-') ? rootSessionId.slice('session-'.length) : null;
  if (!ownerId || !Object.hasOwn(agentLabels, ownerId)) return rootSessionId;
  return `${agentLabels[ownerId]}互动线`;
}

function overlapCount(left, right) {
  return left.filter((member) => right.includes(member)).length;
}

function positionLabel(position) {
  return `v${position.worldVersion}:${position.entryIndex}`;
}

function comparePositions(left, right) {
  return left.worldVersion - right.worldVersion || left.entryIndex - right.entryIndex;
}

function compareText(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

function encodeLineKey(key) {
  return `${key.rootSessionId}\0${key.topologyVersion}`;
}

function lineLabel(key) {
  return `${key.rootSessionId} @ v${key.topologyVersion}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll('`', '&#96;');
}
