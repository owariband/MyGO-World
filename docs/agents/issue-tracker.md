# Issue tracker: Local Markdown

Issues and specs for this repo live as markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`
- Triage state is recorded as a `Status:` line near the top
- Comments append under a `## Comments` heading

## Publishing and fetching

When publishing, create files under `.scratch/<feature-slug>/`. When fetching, read the referenced local file.

## Wayfinding operations

- Map: `.scratch/<effort>/map.md`
- Child ticket: `.scratch/<effort>/issues/NN-<slug>.md`
- Blocking: recorded with `Blocked by: NN, NN`
- Frontier: the first open, unblocked and unclaimed ticket
- Claim: change `Status:` to `claimed`
- Resolve: append an answer, change `Status:` to `resolved`, and update the map
