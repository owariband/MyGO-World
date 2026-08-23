# Dynamic Render extension

Dynamic Render is a temporary technical name. The extension adds an optional timeline renderer around the original MyGO/WebGAL bundle and does not modify the engine bundle under assets.

This extension lives in `generative_go_world`. By default, development commands use the sibling `../MyGO_v3.1.1_ForScript` directory as the WebGAL runtime and asset root. Set `WEBGAL_ROOT` to an absolute path when using a different checkout layout.

## Run

    npm install
    npm run dynamic -- --project rain-after

The extension reads projects/rain-after/timeline.json:

- the timeline groups render segments by event;
- each segment progresses through ready, loading, playing, and played;
- selecting an event starts its first unplayed segment;
- when no render is ready, the stage stays black;
- valid timeline edits are reloaded, while invalid edits only report diagnostics.

## Boundaries

- In the current fixture-only MVP, this extension temporarily owns the loaded timeline and its in-memory playback queue. After Agent Runtime integration, world/Event truth belongs to the external Runtime; this extension owns only RenderJob validation, compilation, playback queue/status, and Viewer Cursor state.
- Structured render input reuses the existing asset and Live2D capability validation.
- The extension uses the fixed bundle's /api/webgalsync and TEMP_SCENE behavior.
- MyGO 3.1.1 and WebGAL 4.5.19 need protocol contract tests because this is not a stable public plugin API.
- The MVP restarts an unfinished render when the viewer switches back; it does not promise character-level resume.
