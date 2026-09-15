# Diagrams

The docs embed **pre-rendered PNGs** from this folder so they display in **any** Markdown viewer —
PyCharm, VS Code, Cursor, Azure DevOps Wiki, GitHub — with **no plugin or setup**.

The editable source for every diagram is [Mermaid](https://mermaid.js.org/), kept in
[`src/`](src/). PNGs are the generated artifact; **edit the `.mmd`, not the `.png`.**

## Regenerate

Requires Node (for `npx`). No global install needed.

```bash
# from docs/diagrams/
for f in src/*.mmd; do
  name=$(basename "$f" .mmd)
  npx -y @mermaid-js/mermaid-cli \
    -i "$f" -o "$name.png" \
    -c src/mermaid-config.json \
    -p src/puppeteer-config.json \
    -b white -s 3
done
```

- `-b white` bakes a white background so text is readable in light **and** dark editor themes.
- `-s 3` renders at 3× for crisp text.
- `src/mermaid-config.json` sets the shared font/theme; `src/puppeteer-config.json` passes
  `--no-sandbox` for headless environments.

Shared colour convention: **blue** = source / structure, **green** = target / success,
**amber** = staging / decision gate, **purple** = governance, **red** = fail-closed / danger.

## Diagram index

| PNG | Used in | Shows |
|-----|---------|-------|
| `overview.png` | root README | Source UC → bundle → governed target shells |
| `three-utilities.png` | root README · ARCHITECTURE | The three utilities in a region move |
| `pick-your-path.png` | docs/README | Which guide to read |
| `happy-path.png` | docs/README | End-to-end dry-run → live flow |
| `big-picture.png` | ARCHITECTURE | End-to-end working model |
| `pipeline-stages.png` | ARCHITECTURE | Inventory / export / import |
| `connectivity-modes.png` | ARCHITECTURE | `direct` vs `airgap` |
| `dependency-order.png` | ARCHITECTURE | Creation order (functions → tables → gov → sweep → views) |
| `fail-closed.png` | ARCHITECTURE | Governed-table fail-closed decision |
| `incremental.png` | ARCHITECTURE | Delta create/update/skip; removals report-only |
| `config-flow.png` | CONFIGURATION_GUIDE | Widgets / job params → validated Config |
| `mapping-inputs.png` | CONFIGURATION_GUIDE | The three location/mapping inputs + precedence |
| `permissions.png` | PERMISSIONS_GUIDE | The two catalog-scoped SPs |
| `which-mode.png` | RUNBOOK | Choose `direct` vs `airgap` |
| `direct-sequence.png` | RUNBOOK | Direct-mode step sequence |
| `airgap-sequence.png` | RUNBOOK | Airgap-mode step sequence + handoff |
| `retry-loop.png` | RUNBOOK | Fix prerequisite → additive re-run |
