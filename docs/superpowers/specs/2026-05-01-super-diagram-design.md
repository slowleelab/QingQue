# Super-Diagram Skill Design Spec

Fuses fireworks-tech-graph (diagram knowledge) with drawio MCP (interactive canvas) into one unified skill.

## 1. Overview

**Super-diagram** is a production-quality diagram generation skill that combines:
- **Brain**: fireworks-tech-graph's knowledge system — 15+ diagram types, shape vocabulary, arrow semantics, 7 styles, layout rules, validation
- **Hands**: drawio MCP tools — interactive canvas, real-time preview, drag-to-edit, multi-format export
- **Eyes**: mandatory visual self-review via image reading — every diagram must pass visual inspection before delivery

Replaces both `fireworks-tech-graph` and `bruce-drawio` as the single diagram skill.

## 2. When to Use

- User asks to create any technical diagram (architecture, flowchart, sequence, ER, UML, etc.)
- User wants to modify an existing diagram
- User says "draw", "diagram", "chart", "画图", "架构图", "流程图", etc.

## 3. Directory Structure

```
~/.claude/skills/super-diagram/
  SKILL.md                              # Core workflow + decision logic (~500 lines)
  references/
    style-1-flat-icon-drawio.md         # Style 1 → drawio full style strings + color tokens
    style-2-dark-terminal-drawio.md     # Style 2 → drawio full style strings + color tokens
    style-3-blueprint-drawio.md         # Style 3 → drawio full style strings + color tokens
    style-4-notion-clean-drawio.md      # Style 4 → drawio full style strings + color tokens
    style-5-glassmorphism-drawio.md     # Style 5 → drawio full style strings + color tokens
    style-6-claude-official-drawio.md   # Style 6 → drawio full style strings + color tokens
    style-7-openai-drawio.md            # Style 7 → drawio full style strings + color tokens
    style-diagram-matrix.md             # Which style suits which diagram type
    shape-vocabulary-drawio.md          # Complete shape vocabulary → drawio style strings + mxCell templates
    arrow-semantics-drawio.md           # Arrow semantics → drawio edge style mapping
    diagram-templates-drawio.md         # 15+ diagram types → drawio XML skeletons + layout rules
    drawio-file-structure.md            # Complete mxfile/mxGraphModel wrapper + page settings
    svg-output.md                       # SVG output rules (Python list method, validation, error recovery)
    cli-export.md                       # draw.io CLI detection (macOS/Windows/Linux) + export flags
    icons.md                            # Product icon colors (copied from fireworks, self-contained)
```

All references are self-contained — no dependency on other skills.

**Why 7 separate style files instead of 1**: Each style file contains full drawio style strings, font settings, arrow marker definitions, and icon accent colors. Loading only the needed style saves tokens. This follows fireworks' proven pattern and the "load on demand" philosophy.

## 4. SKILL.md Structure (~500 lines)

```
1. Overview                — positioning + relationship to replaced skills
2. When to Use             — trigger conditions
3. Workflow (9 steps)      — each step with input/output/decision rules
4. Sub-diagram Strategy    — main diagram + detail sub-diagrams (总-分)
5. Visual Review (MANDATORY) — checklist + execution method + fix loop
6. Editing Existing Diagrams — get_diagram → modify → review → export
7. Diagram Types           — quick reference table (type + one-liner + which reference to load)
8. Style Selection         — quick reference table (style + best use cases)
9. Constraints             — hard rules (no crossings, no manual waypoints, sub-diagram depth, mandatory review)
10. Output Paths           — mode selection + fallback chain
11. Common Mistakes        — frequent errors + fixes
```

## 5. Workflow (9 Steps)

```
Step 1: Classify
  - Determine diagram type from user description
  - Load references/diagram-templates-drawio.md for layout rules

Step 2: Extract
  - Identify nodes, edges, layers, groups from user description
  - Label each node with semantic type (User, LLM, Agent, DB, Tool, etc.)

Step 3: Complexity Check
  - Plan initial layout, simulate edge routes
  - If edges would cross OR a node contains internal complexity → plan sub-diagrams
  - Use layout-based crossing detection (not simple ratio threshold)

Step 4: Load References
  - By diagram type → load diagram-templates-drawio.md
  - By style → load the specific style-N-drawio.md (NOT all 7)
  - By shape needs → load shape-vocabulary-drawio.md
  - By arrow types → load arrow-semantics-drawio.md
  - By product icons → load icons.md

Step 5: Map to Drawio
  - Shape vocabulary → drawio style strings
  - Arrow semantics → drawio edge style
  - Style → color tokens (fillColor, strokeColor, fontColor, etc.)
  - Special shapes: hexagon (Agent), cylinder3 (DB), rhombus (Decision), etc.
  - Assemble complete mxGraphModel XML (load drawio-file-structure.md for wrapper)

Step 6: Layout Planning
  - Assign coordinates ensuring:
    - No edge crossings (primary constraint)
    - No node overlaps
    - Grid-aligned (multiples of 10)
    - Generous spacing (80px between node edges)
  - If layout planning reveals potential crossings → restructure or split into sub-diagrams before rendering (see Section 6)

Step 7: Generate
  Interactive mode (preferred):
    1. start_session (opens browser canvas)
    2. create_new_diagram(xml=<complete mxGraphModel XML>) — sends full diagram in one shot
    3. If adjustments needed later: get_diagram → edit_diagram

  File mode (MCP fallback):
    1. Assemble complete .drawio XML with full mxfile wrapper (load drawio-file-structure.md)
    2. Write to disk

  SVG mode (final fallback):
    1. Load svg-output.md for SVG generation rules
    2. Generate SVG using Python list method
    3. Validate with rsvg-convert
    4. Export PNG

  Chinese labels: ensure encoding="UTF-8" and html=1 in all nodes

Step 8: Visual Review (MANDATORY — see Section 7)
  - Cannot be skipped
  - Interactive mode: export_diagram PNG → Read image → visual check
  - File mode: draw.io CLI export PNG → Read image → visual check
  - CLI unavailable: export and warn user "not visually verified"

Step 9: Export
  - drawio format (always) — editable in draw.io desktop/web
  - PNG (always) — via export_diagram or draw.io CLI or rsvg-convert
  - SVG (on request) — via export_diagram or rsvg-convert
```

## 6. Sub-diagram Strategy (总-分)

**Core principle**: Main diagram shows the primary framework and key relationships. Sub-diagrams expand details for complex nodes.

### Structure

- **Main diagram**: Top-level nodes + core relationships only. Must be clean with no crossings.
- **Sub-diagrams**: Each expands one complex node from the main diagram into its internal structure.

### Rules

1. Always draw main diagram first — keep it at high-level granularity
2. Identify nodes that "contain internal complexity" (a service with multiple internal components, a data layer with multiple stores, etc.)
3. For each such node, generate a separate sub-diagram showing its internals
4. **Max depth: 2 levels** — main diagram → sub-diagram. No sub-sub-diagrams.
5. Linking: main diagram node gets a text annotation ("详见子图N: filename.drawio"). The drawio `link` attribute uses `file://` paths where possible, but this is platform-dependent — text annotation is the reliable fallback.

### When to split

- After layout planning, if any edges would cross → simplify main diagram by collapsing complex nodes, move detail to sub-diagrams
- If a node has >3 internal connections that matter → extract to sub-diagram
- If user describes "this part has more detail" → extract to sub-diagram
- Comparison matrices with >5 columns → split into two diagrams

### Example

User: "画一个微服务电商架构"

- **Main diagram**: Client → API Gateway → [Order Service, Payment Service, User Service] → [PostgreSQL, Redis, Kafka] (6 nodes, ~7 edges, clean)
- **Sub-diagram 1**: "Order Service 内部" — Controller → Service → Repository → Event Publisher
- **Sub-diagram 2**: "数据层关系" — PostgreSQL (order_db, user_db), Redis (cache, session), Kafka (topics)

## 7. Visual Review (MANDATORY)

### Checklist

Every generated diagram MUST pass these checks:

- [ ] No edge crossings — zero tolerance
- [ ] No edge passes through node interior
- [ ] No label collisions (node labels, edge labels)
- [ ] No node overlap
- [ ] No text overflow (text fits within shape with 8px padding)
- [ ] Legend does not cover content
- [ ] Edge endpoints connect to shape edges, not floating
- [ ] Edge labels have background rects
- [ ] Consistent style within diagram (colors, fonts, sizes)

### Execution

```
Interactive mode:
  1. export_diagram(format="png", path="./review.png")
  2. Read("./review.png") — use visual model to inspect
  3. If issues found:
     a. get_diagram (fetch current state including any user manual edits — MUST do this before EVERY edit_diagram call)
     b. edit_diagram (apply fixes)
     c. export_diagram PNG → Read → re-check
  4. Max 3 fix cycles

File mode:
  1. Detect draw.io CLI (load references/cli-export.md for platform paths)
  2. Export: "$DRAWIO" -x -f png --scale 2 -o review.png diagram.drawio
  3. Read("./review.png") — use visual model to inspect
  4. If issues found → edit .drawio XML → re-export → re-check
  5. Max 3 fix cycles

CLI unavailable:
  1. Export .drawio file
  2. Tell user: "⚠️ Diagram not visually verified. Please open in draw.io and check for: crossing edges, overlapping labels, text overflow."
```

### After 3 cycles

If issues remain after 3 fix cycles:
- Export current best version (PNG + .drawio)
- Attach the review screenshot
- List remaining issues
- Let user decide: accept, manually fix, or describe what to change

## 8. Editing Existing Diagrams

When user asks to modify an existing diagram:

```
1. get_diagram — fetch current XML from browser (includes user's manual edits)
   OR read the .drawio file from disk

2. Parse structure — identify cell IDs, positions, styles, edges

3. Apply requested changes — use edit_diagram (update/add/delete operations)
   OR edit the .drawio XML directly
   IMPORTANT: Before EVERY edit_diagram call, call get_diagram first to fetch
   the latest state, including any manual edits the user made in the browser.

4. Visual Review (MANDATORY) — same checklist as Section 7

5. Export
```

## 9. Constraints (Hard Rules)

1. **No edge crossings** — zero tolerance. If layout planning reveals potential crossings, restructure or split into sub-diagrams before rendering.
2. **No manual waypoints** — do not add manual waypoints to force-route edges around obstacles. Use `orthogonalEdgeStyle` and let draw.io handle routing automatically. If auto-routing produces crossings, simplify the diagram layout or split into sub-diagrams.
3. **Mandatory visual review** — every diagram, no exceptions.
4. **Sub-diagram max depth: 2** — main → sub only. No deeper nesting.
5. **Self-contained references** — all reference files live under super-diagram/references/. No external skill dependencies.
6. **Chinese support** — always use `encoding="UTF-8"` and `html=1"` for Chinese labels in drawio XML.
7. **Consistent naming** — .drawio files use lowercase + hyphens, no Chinese/special chars in filenames.
8. **Comparison matrix max columns: 5** — beyond that, split into two diagrams.

## 10. Output Paths

### Mode selection

```
1. Try MCP interactive mode:
   - start_session → if success → create_new_diagram(xml=complete_XML)
   - Advantage: real-time preview, user can drag-to-adjust
   - Subsequent edits: get_diagram → edit_diagram

2. If MCP fails, fall back to file mode:
   - Write complete .drawio XML to disk (use drawio-file-structure.md wrapper)
   - Tell user to open in draw.io desktop or https://app.diagrams.net

3. If user explicitly requests SVG or PNG only (no .drawio needed):
   - Generate SVG directly (load references/svg-output.md)
   - Export PNG via rsvg-convert
```

### Export formats

| Format | When | Method |
|--------|------|--------|
| .drawio | Always | MCP export_diagram or Write tool |
| .png | Always | MCP export_diagram or draw.io CLI or rsvg-convert |
| .svg | On request | MCP export_diagram or direct SVG generation |

### CLI detection and export

For file-mode visual review and export, detect draw.io CLI:
- Load `references/cli-export.md` for platform-specific paths (macOS, Windows, Linux)
- Export flags: `-x` (no GUI), `-f png/svg/pdf`, `-o path`, `--scale 2`, `--border 20`, `--crop`
- If CLI not found, suggest installation (brew/winget/snap) without auto-installing

## 11. Style-to-Drawio Mapping (Summary)

| Style # | Name | Background | drawio fillColor | drawio strokeColor | Best For |
|---------|------|-----------|-------------------|-------------------|----------|
| 1 | Flat Icon | #ffffff | #ffffff / accent tints | #d1d5db | Default, blogs, docs |
| 2 | Dark Terminal | #0f0f1a | #1a1a2e / dark tints | #4a5568 | Dev blogs, GitHub |
| 3 | Blueprint | #0a1628 | #0f1d32 / blue tints | #2563eb | Architecture docs |
| 4 | Notion Clean | #ffffff | #ffffff / minimal | #e5e7eb | Notion, inline docs |
| 5 | Glassmorphism | dark gradient | gradient + opacity | semi-transparent | Presentations |
| 6 | Claude Official | #f8f6f3 | #fff7ed / warm tints | #d97706 | Anthropic-style |
| 7 | OpenAI Official | #ffffff | #ffffff / minimal | #10a37f | OpenAI-style |

Full style strings, font settings, arrow markers → load `references/style-N-drawio.md`

## 12. Shape-to-Drawio Mapping (Summary)

| Concept | Shape | drawio style |
|---------|-------|-------------|
| User / Human | Circle + body | `ellipse` + `path` stick figure (avoid mxgraph.cisco dependency) |
| LLM / Model | Rounded rect, double border | `rounded=1;double=1;` + inner/outer strokeColor |
| Agent / Orchestrator | Hexagon | `shape=hexagon;` |
| Memory (short-term) | Rounded rect, dashed | `rounded=1;dashed=1;dashPattern=5 3;` |
| Memory (long-term) / DB | Cylinder | `shape=cylinder3;size=10;` |
| Vector Store | Cylinder with lines | `shape=cylinder3;size=10;` + inner lines |
| Graph DB | Circle cluster | 3 overlapping `ellipse` elements |
| Tool / Function | Rect with icon | `rounded=1;` + emoji or image |
| API / Gateway | Hexagon (single border) | `shape=hexagon;` |
| Queue / Stream | Pipe/tube | custom `path` or `rounded=1;` with gradient |
| Decision | Diamond | `rhombus;` |
| Process / Step | Rounded rect | `rounded=1;whiteSpace=wrap;html=1;` |
| External Service | Rect, dashed border | `rounded=1;dashed=1;` |
| Container / Group | Rect, dashed, fill opacity | `rounded=1;dashed=1;fillOpacity=5;` |
| File / Document | Folded-corner rect | custom `path` with fold |
| Browser / UI | Rect with titlebar | `rounded=1;` + titlebar rect + traffic-light dots |

Full templates with mxCell XML → load `references/shape-vocabulary-drawio.md`

## 13. Arrow Semantics (Summary)

| Flow Type | Color | drawio strokeColor | Width | Dash | drawio dashPattern |
|-----------|-------|-------------------|-------|------|-------------------|
| Primary data flow | #2563eb | #2563eb | 2 | solid | (none) |
| Control / trigger | #ea580c | #ea580c | 1.5 | solid | (none) |
| Memory read | #059669 | #059669 | 1.5 | solid | (none) |
| Memory write | #059669 | #059669 | 1.5 | dashed | `5 3` |
| Async / event | #6b7280 | #6b7280 | 1.5 | dashed | `4 2` |
| Embedding / transform | #7c3aed | #7c3aed | 1 | solid | (none) |
| Feedback / loop | #7c3aed | #7c3aed | 1.5 | curved | (none) + `curved=1;` |

All edges: `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;`

Use `orthogonalEdgeStyle` for automatic routing — do NOT add manual waypoints. If auto-routing produces crossings, simplify layout or split into sub-diagrams.

Include legend when 2+ arrow types used.

Full mapping → load `references/arrow-semantics-drawio.md`

## 14. Diagram Types Quick Reference

| Type | Description | Layout Direction | Load Reference |
|------|-------------|-----------------|---------------|
| Architecture | Services/components in layers | Top-to-bottom or left-to-right | diagram-templates-drawio.md#architecture |
| Data Flow | Data transformation pipelines | Left-to-right | diagram-templates-drawio.md#data-flow |
| Flowchart | Sequential decision/process | Top-to-bottom | diagram-templates-drawio.md#flowchart |
| Agent Architecture | AI agent reasoning + tools + memory | Top-to-bottom | diagram-templates-drawio.md#agent |
| Memory Architecture | Memory read/write paths | Top-to-bottom | diagram-templates-drawio.md#memory |
| Sequence | Time-ordered messages | Left-to-right participants | diagram-templates-drawio.md#sequence |
| Comparison Matrix | Side-by-side comparison (max 5 cols) | Grid | diagram-templates-drawio.md#comparison |
| Timeline / Gantt | Phases and milestones | Left-to-right time axis | diagram-templates-drawio.md#timeline |
| Mind Map | Radial concept expansion | Center-outward | diagram-templates-drawio.md#mindmap |
| Class Diagram (UML) | Classes, attributes, methods | Top-to-bottom | diagram-templates-drawio.md#class |
| Use Case (UML) | Actors and system functions | Centered | diagram-templates-drawio.md#use-case |
| State Machine | States and transitions | Top-to-bottom | diagram-templates-drawio.md#state-machine |
| ER Diagram | Entities and relationships | Grid | diagram-templates-drawio.md#er |
| Network Topology | Network infrastructure | Tiered top-to-bottom | diagram-templates-drawio.md#network |

### UML Coverage Map

| UML Diagram | Supported As | Notes |
|-------------|-------------|-------|
| Class | Class Diagram | Full UML notation |
| Component | Architecture Diagram | Use colored fills per component type |
| Deployment | Architecture Diagram | Add node/instance labels |
| Package | Architecture Diagram | Use dashed grouping containers |
| Composite Structure | Architecture Diagram | Nested rects within components |
| Object | Class Diagram | Instance boxes with underlined name |
| Use Case | Use Case Diagram | Full actor/ellipse/relationship |
| Activity | Flowchart / Process Flow | Add fork/join bars |
| State Machine | State Machine Diagram | Full UML notation |
| Sequence | Sequence Diagram | Add alt/opt/loop frames |
| Communication | — | Approximate with Sequence (swap axes) |
| Timing | Timeline | Adapt time axis |
| Interaction Overview | Flowchart | Combine activity + sequence fragments |

## 15. Common Mistakes

| Mistake | Fix |
|---------|-----|
| Edges crossing through nodes | Re-layout or collapse to sub-diagram |
| Too many edges on one node | Use sub-diagram to expand that node |
| Labels overflowing shapes | Increase node width or use `whiteSpace=wrap;html=1;` |
| Chinese text garbled | Ensure `encoding="UTF-8"` and `html=1` |
| Inconsistent style within one diagram | Pick one style, apply color tokens consistently |
| Skipping visual review | Never skip. It's mandatory. |
| Forgetting legend for multi-color arrows | Add legend in bottom-left when 2+ arrow colors used |
| Sub-diagrams too deep | Max 2 levels. If still complex, simplify the sub-diagram. |
| edit_diagram without get_diagram first | ALWAYS call get_diagram before edit_diagram — skipping loses user edits |
| Using mxgraph.cisco shapes | Avoid — may not be available. Use basic drawio shapes instead. |
| Calling create_new_diagram with empty XML | Must send complete mxGraphModel XML in one shot |
| Adding manual waypoints for edge routing | Don't. Use orthogonalEdgeStyle. If crossings occur, simplify or split. |
