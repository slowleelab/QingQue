# Super-Diagram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a unified diagram skill that fuses fireworks-tech-graph's knowledge system with drawio MCP's interactive canvas and mandatory visual self-review.

**Architecture:** Core SKILL.md (~500 lines) with heavy knowledge offloaded to `references/` files loaded on demand. 7 style files adapted from fireworks SVG styles to drawio style strings. Shape vocabulary and arrow semantics translated from SVG patterns to drawio mxCell XML. Three-tier output: MCP interactive → .drawio file → SVG fallback.

**Tech Stack:** Claude Code skill (markdown), drawio MCP tools (start_session, create_new_diagram, edit_diagram, get_diagram, export_diagram), rsvg-convert (SVG fallback)

---

## File Structure

```
~/.claude/skills/super-diagram/
  SKILL.md                                # Core workflow + decision logic
  references/
    style-1-flat-icon-drawio.md           # Style 1 full drawio style strings
    style-2-dark-terminal-drawio.md       # Style 2 full drawio style strings
    style-3-blueprint-drawio.md           # Style 3 full drawio style strings
    style-4-notion-clean-drawio.md        # Style 4 full drawio style strings
    style-5-glassmorphism-drawio.md       # Style 5 full drawio style strings
    style-6-claude-official-drawio.md     # Style 6 full drawio style strings
    style-7-openai-drawio.md              # Style 7 full drawio style strings
    style-diagram-matrix.md               # Which style suits which diagram type
    shape-vocabulary-drawio.md            # Complete shape → drawio style + mxCell templates
    arrow-semantics-drawio.md             # Arrow semantics → drawio edge style mapping
    diagram-templates-drawio.md           # 15+ diagram types → drawio XML skeletons + layout rules
    drawio-file-structure.md              # Complete mxfile wrapper structure
    svg-output.md                         # SVG output rules (Python list, validation, error recovery)
    cli-export.md                         # draw.io CLI detection + export flags
    icons.md                              # Product icon colors (copied from fireworks)
```

---

### Task 1: Create skill directory and SKILL.md skeleton

**Files:**
- Create: `~/.claude/skills/super-diagram/SKILL.md`
- Create: `~/.claude/skills/super-diagram/references/` (directory)

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p ~/.claude/skills/super-diagram/references
```

- [ ] **Step 2: Write SKILL.md**

Write the complete SKILL.md (~500 lines) with all 11 sections from the spec:

1. Overview — positioning as replacement for fireworks-tech-graph + bruce-drawio
2. When to Use — trigger conditions (diagram, draw, chart, 画图, 架构图, etc.)
3. Workflow (9 steps) — each with input/output/decision rules:
   - Step 1: Classify diagram type → load diagram-templates-drawio.md
   - Step 2: Extract nodes, edges, layers, groups
   - Step 3: Complexity check — layout simulation, crossing detection → plan sub-diagrams
   - Step 4: Load references (style-N, shapes, arrows, icons)
   - Step 5: Map to drawio — shapes→style strings, arrows→edge style, style→colors
   - Step 6: Layout planning — coordinates, no crossings, grid-aligned, 80px spacing
   - Step 7: Generate — MCP: `start_session` → `create_new_diagram(xml=complete)`, File: `.drawio` with wrapper, SVG: Python list method
   - Step 8: Visual Review (MANDATORY) — export PNG → Read image → check → fix loop (max 3)
   - Step 9: Export — .drawio + PNG always, SVG on request
4. Sub-diagram Strategy (总-分) — main diagram = framework, sub-diagrams = detail expansion, max depth 2, text annotation + optional link
5. Visual Review (MANDATORY) — 9-item checklist, execution per mode, fix loop with `get_diagram` before every `edit_diagram`, 3-cycle cap
6. Editing Existing Diagrams — `get_diagram` → parse → `edit_diagram` (with `get_diagram` before every call) → review → export
7. Diagram Types — quick reference table (14 types + UML coverage map)
8. Style Selection — quick reference table (7 styles + best use cases)
9. Constraints — hard rules: no crossings, no manual waypoints (orthogonalEdgeStyle is OK), mandatory review, max depth 2, self-contained, UTF-8+html=1, lowercase filenames, max 5 comparison columns
10. Output Paths — MCP interactive → .drawio file → SVG fallback, with CLI detection
11. Common Mistakes — 12-entry table including edit_diagram without get_diagram, cisco shapes, empty XML, manual waypoints

Key content rules for SKILL.md:
- `create_new_diagram` MUST receive complete mxGraphModel XML in one shot
- Every `edit_diagram` call MUST be preceded by `get_diagram`
- Constraint 2 reworded: "No manual waypoints — use orthogonalEdgeStyle and let draw.io handle routing. If auto-routing produces crossings, simplify or split."
- Visual review fix loop: `get_diagram` → `edit_diagram` → `export_diagram` → Read → re-check
- Chinese labels: `encoding="UTF-8"` and `html=1`

- [ ] **Step 3: Verify SKILL.md is complete**

Run: `wc -l ~/.claude/skills/super-diagram/SKILL.md`
Expected: ~450-550 lines

Run: `grep -c '##' ~/.claude/skills/super-diagram/SKILL.md`
Expected: 11+ section headers

---

### Task 2: Create drawio-file-structure.md

**Files:**
- Create: `~/.claude/skills/super-diagram/references/drawio-file-structure.md`

- [ ] **Step 1: Write drawio-file-structure.md**

Document the complete XML wrapper structure required for valid .drawio files:

```markdown
# Drawio File Structure

## Complete .drawio File Wrapper

Every .drawio file MUST use this structure:

<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" agent="super-diagram-skill" version="21.0.0" type="device">
  <diagram name="DiagramName" id="diagram-1">
    <mxGraphModel dx="1422" dy="762"
                   grid="1" gridSize="10"
                   guides="1" tooltips="1" connect="1"
                   arrows="1" fold="1"
                   page="1" pageScale="1"
                   pageWidth="1600" pageHeight="1200"
                   math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <!-- nodes and edges here -->
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>

## mxGraphModel for MCP create_new_diagram

When using MCP create_new_diagram, provide only the mxGraphModel content:

<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <!-- nodes and edges here -->
  </root>
</mxGraphModel>

## Key Attributes

| Attribute | Description | Default |
|-----------|-------------|---------|
| dx | Horizontal scroll offset | 1422 |
| dy | Vertical scroll offset | 762 |
| grid | Show grid | 1 |
| gridSize | Grid cell size (px) | 10 |
| page | Show page boundary | 1 |
| pageWidth | Canvas width | 1600 |
| pageHeight | Canvas height | 1200 |

## Node Template

<mxCell id="node-1" value="Label"
        style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=14;"
        vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="160" height="60" as="geometry" />
</mxCell>

## Edge Template

<mxCell id="edge-1" value=""
        style="edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0.5;entryY=0;entryDx=0;entryDy=0;"
        edge="1" parent="1" source="node-1" target="node-2">
  <mxGeometry relative="1" as="geometry" />
</mxCell>

## Text Rules

- Multi-line labels: use &#xa; inside value attribute (NOT \n)
- Always set html=1 on nodes
- Chinese text: encoding="UTF-8" on the XML declaration, html=1 on every node

## ID Rules

- IDs "0" and "1" are reserved (root cells)
- User IDs start from "2" or use descriptive names like "node-gateway", "edge-client-to-gw"
- All IDs must be unique within the diagram
```

- [ ] **Step 2: Verify file exists**

Run: `test -f ~/.claude/skills/super-diagram/references/drawio-file-structure.md && echo "OK"`

---

### Task 3: Create style-1-flat-icon-drawio.md

**Files:**
- Create: `~/.claude/skills/super-diagram/references/style-1-flat-icon-drawio.md`

- [ ] **Step 1: Write style-1-flat-icon-drawio.md**

Adapt fireworks' style-1-flat-icon.md from SVG to drawio style strings. Include:

- **Colors table**: Background (#ffffff), Box fill (#ffffff), Box stroke (#d1d5db), Box radius (8px), Text primary (#111827), Text secondary (#6b7280), Accent tints (blue #eff6ff/#dbeafe, red #fef2f2/#fee2e2, green #f0fdf4/#dcfce7, purple #faf5ff/#ede9fe, orange #fff7ed/#fed7aa, teal #f0fdfa/#ccfbf1)
- **Semantic arrow colors**: Flow A main (#2563eb), Flow B alt (#dc2626), Flow C data (#16a34a), Flow D async (#9333ea)
- **Typography**: font-family (Helvetica Neue, Helvetica, Arial, PingFang SC, Microsoft YaHei, SimHei, sans-serif), font-size (14px labels, 12px sub-labels, 16px titles), font-weight (400 normal, 600 semi-bold titles)
- **Drawio style strings**:
  - Standard node: `rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#d1d5db;fontSize=14;fontFamily=Helvetica Neue;`
  - Accent node (blue): `rounded=1;whiteSpace=wrap;html=1;fillColor=#eff6ff;strokeColor=#bfdbfe;fontSize=14;fontFamily=Helvetica Neue;`
  - Container: `rounded=1;dashed=1;whiteSpace=wrap;html=1;fillColor=#ffffff;fillOpacity=5;strokeColor=#d1d5db;fontSize=10;fontFamily=Helvetica Neue;`
  - Edge (blue): `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#2563eb;strokeWidth=2;`
  - Edge (red): `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#dc2626;strokeWidth=1.5;`
  - Edge (green): `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#16a34a;strokeWidth=1.5;`
  - Edge (purple dashed): `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#9333ea;strokeWidth=1.5;dashed=1;dashPattern=5 3;`
- **Legend template**: mxCell XML for a legend box in the bottom-left with colored line + label pairs

- [ ] **Step 2: Verify file**

Run: `grep -c 'drawio' ~/.claude/skills/super-diagram/references/style-1-flat-icon-drawio.md`
Expected: 5+ (multiple drawio style strings present)

---

### Task 4: Create styles 2-7 drawio reference files

**Files:**
- Create: `~/.claude/skills/super-diagram/references/style-2-dark-terminal-drawio.md`
- Create: `~/.claude/skills/super-diagram/references/style-3-blueprint-drawio.md`
- Create: `~/.claude/skills/super-diagram/references/style-4-notion-clean-drawio.md`
- Create: `~/.claude/skills/super-diagram/references/style-5-glassmorphism-drawio.md`
- Create: `~/.claude/skills/super-diagram/references/style-6-claude-official-drawio.md`
- Create: `~/.claude/skills/super-diagram/references/style-7-openai-drawio.md`

- [ ] **Step 1: Write style-2-dark-terminal-drawio.md**

Adapt fireworks' style-2-dark-terminal.md:
- Colors: Background (#0f0f1a), Panel fill (#0f172a), Panel stroke (#334155), Text primary (#e2e8f0), Text secondary (#94a3b8), Accents: Purple #7c3aed/#a855f7, Orange #ea580c/#f97316, Blue #1d4ed8/#3b82f6, Green #059669/#10b981
- Typography: Monospace stack (SF Mono, Fira Code, Cascadia Code, Courier New, Microsoft YaHei, SimHei), 13px labels, 11px sub-labels
- Drawio style strings for: standard node (dark fill), accent nodes (per color), edges (neon-colored)
- Note: drawio cannot do SVG glow effects — use strokeColor with brighter accent color and strokeWidth=2 for emphasis

- [ ] **Step 2: Write style-3-blueprint-drawio.md**

Adapt fireworks' style-3-blueprint.md:
- Colors: Background (#0a1628), Panel fill (#0d1f3c), Panel stroke (#00b4d8), Text primary (#caf0f8), Cyan accent (#00b4d8/#48cae4)
- Typography: Monospace (Courier New, Lucida Console, Microsoft YaHei, SimHei), 13px labels, 10px annotations, uppercase section headers
- Drawio style strings: sharp corners (rounded=0 or rounded=1;arcSize=5), cyan strokes, dark fills
- Background: drawio page background cannot do SVG grid pattern — set page background to #0a1628 via `background=#0a1628;` in mxGraphModel or use a background rect node

- [ ] **Step 3: Write style-4-notion-clean-drawio.md**

Adapt fireworks' style-4-notion-clean.md:
- Colors: Background (#ffffff), Box fill (#f9fafb), Box stroke (#e5e7eb), Text primary (#111827), Text secondary (#374151), Single arrow color (#3b82f6)
- Typography: System font stack, 14px labels, 11px uppercase type labels
- Drawio style strings: minimal nodes (light gray fill, thin border), single-color edges, no decorative elements
- Design rule: no icons, geometric shapes only, generous whitespace

- [ ] **Step 4: Write style-5-glassmorphism-drawio.md**

Adapt fireworks' style-5-glassmorphism.md:
- Colors: Background gradient (#0d1117→#161b22→#0d1117), Glass card fill (rgba(255,255,255,0.05)→use fillColor=#1a1f26;fillOpacity=10), Glass stroke (rgba(255,255,255,0.15)→strokeColor=#4a5568;strokeOpacity=30), Text primary (#f0f6fc), Accent glows: Blue #58a6ff, Purple #bc8cff, Green #3fb950, Orange #f78166
- Drawio limitations: no backdrop-filter, no SVG radial gradients for ambient glow — simulate with semi-transparent fills and colored strokes
- Drawio style strings: glass nodes (dark fill, low opacity, semi-transparent stroke), colored accent edges with opacity

- [ ] **Step 5: Write style-6-claude-official-drawio.md**

Adapt fireworks' style-6-claude-official.md:
- Colors: Background (#f8f6f3), Node fills by semantic type: Input/Source (#a8c5e6), Agent/Process (#9dd4c7), Infrastructure (#f4e4c1), Storage/State (#e8e6e3), Stroke (#4a4a4a), Text primary (#1a1a1a), Arrow (#5a5a5a)
- Typography: System font stack, 16px node labels, 14px descriptions, 13px arrow labels
- Drawio style strings: rounded=1;arcSize=20 (rx=12 equivalent), per-semantic-type fill colors, strokeWidth=2.5, soft shadow via shadow=1
- Arrow semantics in Claude style: all arrows same color (#5a5a5a), differentiated by dash pattern (solid=read, dashed=write, dotted=control)

- [ ] **Step 6: Write style-7-openai-drawio.md**

Adapt fireworks' style-7-openai.md:
- Colors: Background (#ffffff), Box fill (#ffffff), Box stroke (#e5e5e5), Text primary (#0d0d0d), Text secondary (#6e6e80), Green accent (#10a37f), Blue accent (#1d4ed8), Gray accent (#71717a)
- Typography: System font stack, 16px node labels, 13px descriptions, 12px arrow labels
- Drawio style strings: white-on-white minimal nodes, optional green left-border accent strip (implemented as a thin rect overlay or container+child), thin strokes (strokeWidth=1.5)
- Arrow colors: gray (#71717a) for default, green (#10a37f) for primary/accent

- [ ] **Step 7: Verify all 6 files**

Run: `ls ~/.claude/skills/super-diagram/references/style-*-drawio.md | wc -l`
Expected: 6

---

### Task 5: Create style-diagram-matrix.md

**Files:**
- Create: `~/.claude/skills/super-diagram/references/style-diagram-matrix.md`

- [ ] **Step 1: Write style-diagram-matrix.md**

Copy and adapt from fireworks' style-diagram-matrix.md. For each diagram type, list all 7 styles with suitability rating and notes. This is a direct port — the content is format-agnostic (it maps style↔diagram compatibility, not SVG-specific).

Include all 14 diagram types from the spec: Architecture, Class/ER, Sequence, Flowchart, Mind Map, Data Flow, Use Case, State Machine, Network, Comparison, Timeline, Agent/Memory.

- [ ] **Step 2: Verify file**

Run: `grep -c 'Excellent\|Good\|Poor' ~/.claude/skills/super-diagram/references/style-diagram-matrix.md`
Expected: 50+ (7 styles × 14 types × multiple entries)

---

### Task 6: Create shape-vocabulary-drawio.md

**Files:**
- Create: `~/.claude/skills/super-diagram/references/shape-vocabulary-drawio.md`

- [ ] **Step 1: Write shape-vocabulary-drawio.md**

Translate ALL shapes from fireworks' icons.md + SKILL.md shape vocabulary section into drawio mxCell XML templates. This must be a COMPLETE superset — no shapes dropped.

Shapes to include (each with drawio style string + mxCell XML example):

1. **User / Human** — stick figure via `ellipse` (head) + `path` (body), or use `shape=actor;` with `verticalLabelPosition=bottom`
2. **LLM / Model** — `rounded=1;double=1;strokeColor=#outer;` with inner border approach (drawio `double=1` adds a second border)
3. **Agent / Orchestrator** — `shape=hexagon;`
4. **Memory (short-term)** — `rounded=1;dashed=1;dashPattern=5 3;`
5. **Memory (long-term) / DB** — `shape=cylinder3;size=10;` (drawio built-in cylinder)
6. **Vector Store** — `shape=cylinder3;size=10;` + internal horizontal line overlays
7. **Graph DB** — 3 overlapping `ellipse` elements in a cluster
8. **Tool / Function** — `rounded=1;` + emoji in value or image overlay
9. **API / Gateway** — `shape=hexagon;` (single border, smaller size=50)
10. **Queue / Stream** — `shape=process;` (drawio process shape with side bars) or custom rounded rect
11. **Decision** — `rhombus;`
12. **Process / Step** — `rounded=1;whiteSpace=wrap;html=1;`
13. **External Service** — `rounded=1;dashed=1;`
14. **Container / Group** — `rounded=1;dashed=1;fillOpacity=5;` (use as parent for child nodes)
15. **File / Document** — `shape=partialRectangle;` or custom `path` with fold corner
16. **Browser / UI** — `rounded=1;` with titlebar overlay (small rect at top with traffic-light dots)
17. **Swim Lane Container** — `swimlane;` with `startSize=30;` for header area
18. **Database Cylinder** (product-specific) — `shape=cylinder3;size=10;` with product label
19. **Product Icon Badge** — `ellipse;` circle with product abbreviation text

For each shape, provide:
- Semantic meaning
- drawio style string (complete, ready to use)
- mxCell XML example with placeholder coordinates
- Size recommendations (width, height)
- Notes on drawio-specific behavior

- [ ] **Step 2: Verify all 19 shapes present**

Run: `grep -c '###\|##' ~/.claude/skills/super-diagram/references/shape-vocabulary-drawio.md`
Expected: 19+ shape subsections

---

### Task 7: Create arrow-semantics-drawio.md

**Files:**
- Create: `~/.claude/skills/super-diagram/references/arrow-semantics-drawio.md`

- [ ] **Step 1: Write arrow-semantics-drawio.md**

Translate fireworks' arrow semantics to drawio edge styles:

7 arrow types with complete drawio style strings:

| Flow Type | strokeColor | strokeWidth | dashed | dashPattern | curved | Full style string |
|-----------|-------------|-------------|--------|-------------|--------|-------------------|
| Primary data flow | #2563eb | 2 | 0 | - | 0 | `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#2563eb;strokeWidth=2;` |
| Control / trigger | #ea580c | 1.5 | 0 | - | 0 | `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#ea580c;strokeWidth=1.5;` |
| Memory read | #059669 | 1.5 | 0 | - | 0 | `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#059669;strokeWidth=1.5;` |
| Memory write | #059669 | 1.5 | 1 | 5 3 | 0 | `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#059669;strokeWidth=1.5;dashed=1;dashPattern=5 3;` |
| Async / event | #6b7280 | 1.5 | 1 | 4 2 | 0 | `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#6b7280;strokeWidth=1.5;dashed=1;dashPattern=4 2;` |
| Embedding / transform | #7c3aed | 1 | 0 | - | 0 | `endArrow=classic;edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#7c3aed;strokeWidth=1;` |
| Feedback / loop | #7c3aed | 1.5 | 0 | - | 1 | `endArrow=classic;curved=1;rounded=1;html=1;strokeColor=#7c3aed;strokeWidth=1.5;` |

Also include:
- Edge routing rules: use `edgeStyle=orthogonalEdgeStyle`, no manual waypoints, specify exitX/exitY/entryX/entryY for source/target connection points
- Bidirectional edges: use opposite connection points (e.g., exitX=1,entryX=0 for A→B and exitX=0,entryX=1 for B→A)
- Arrow label background: add `labelBackgroundColor=#ffffff;` (or matching background color) to edge style
- Legend mxCell XML template for diagrams with 2+ arrow types
- Rules for when to include a legend

- [ ] **Step 2: Verify all 7 arrow types present**

Run: `grep -c 'strokeColor=' ~/.claude/skills/super-diagram/references/arrow-semantics-drawio.md`
Expected: 7+ (one per arrow type)

---

### Task 8: Create diagram-templates-drawio.md

**Files:**
- Create: `~/.claude/skills/super-diagram/references/diagram-templates-drawio.md`

- [ ] **Step 1: Write diagram-templates-drawio.md**

This is the largest reference file. For each of the 14 diagram types, provide:

1. **Architecture** — Layout: horizontal layers (Client→Gateway→Services→Data). Container nodes for each layer. Nodes in rows within containers. ViewBox: 1600×1200. Include UML component/deployment/package diagram notes.
2. **Data Flow** — Layout: left-to-right. Label every edge with data type. Wider arrows for primary paths. Dashed for control flows. Color by data category.
3. **Flowchart** — Layout: top-to-bottom. Diamond for decisions, rounded rects for processes, parallelograms for I/O. Grid snap: x=120px, y=80px. Short labels (≤3 words).
4. **Agent Architecture** — Layers: Input→Agent Core→Memory→Tools→Output. Cyclic arrow for reasoning loop. Separate memory types visually.
5. **Memory Architecture** — Separate write/read paths (different colors). Memory tiers: Working→Short-term→Long-term→External. Label operations: store(), retrieve(), forget().
6. **Sequence** — Participants as vertical lifelines. Messages as horizontal arrows top-to-bottom. Activation boxes. Loop/alt/alt frames.
7. **Comparison Matrix** — Columns = systems, Rows = attributes. Max 5 columns. Row height: 40px, Header: 50px. Checkmark cells with tinted background.
8. **Timeline / Gantt** — X=time, Y=items. Bars: rounded rects colored by category. Milestone markers: diamond shape.
9. **Mind Map** — Central node at (480,280). First-level: evenly distributed 360°/N. Second-level: 30-45° offset. Curved paths.
10. **Class Diagram** — 3-compartment rect (name/attributes/methods). Min width 160px. Relationships: inheritance, implementation, association, aggregation, composition, dependency.
11. **Use Case** — Actor: stick figure outside boundary. Use case: ellipse. System boundary: dashed rect. Include/extend/generalization relationships.
12. **State Machine** — States: rounded rects. Initial: filled circle. Final: circle-in-circle. Choice: diamond. Transitions with event[guard]/action labels.
13. **ER Diagram** — Entity: rect with header. PK underlined, FK italic. Relationship: diamond. Cardinality labels.
14. **Network Topology** — Devices: icon-like rects. Connections: labeled lines. Subnets: dashed containers. Tiered layout.

For each type include:
- Layout direction and spacing rules
- Standard node sizes (width×height)
- ViewBox/page dimensions
- Key drawio shapes used
- mxCell XML skeleton (minimal example with 2-3 nodes and 1-2 edges)

Also include the UML Coverage Map from the spec.

- [ ] **Step 2: Verify all 14 types present**

Run: `grep -c '^## ' ~/.claude/skills/super-diagram/references/diagram-templates-drawio.md`
Expected: 14+

---

### Task 9: Create svg-output.md

**Files:**
- Create: `~/.claude/skills/super-diagram/references/svg-output.md`

- [ ] **Step 1: Write svg-output.md**

Port fireworks' SVG generation rules for the final fallback mode:

- **Mandatory Python List Method**: Always generate SVG using Python script with lines=[] list, write to file. Prevents truncation and syntax errors.
- **SVG Structure**: viewBox, defs, markers, fonts (no @import), background rect
- **Validation**: `rsvg-convert file.svg -o /dev/null 2>&1`
- **Export PNG**: `rsvg-convert -w 1920 file.svg -o file.png`
- **Error Recovery**: 1st error → targeted fix; 2nd error → switch method; 3rd error → stop and report
- **Common Syntax Errors**: yt-anchor, missing y, unquoted fill, missing marker-end, missing closing tags
- **Visual Self-Review for SVG**: same checklist as drawio mode, but using rsvg-convert PNG output
- **Chinese Font Stack**: embed in `<style>font-family: ...</style>` with PingFang SC, Microsoft YaHei, SimHei

- [ ] **Step 2: Verify file**

Run: `grep -c 'rsvg-convert' ~/.claude/skills/super-diagram/references/svg-output.md`
Expected: 3+ (validation, export, review)

---

### Task 10: Create cli-export.md

**Files:**
- Create: `~/.claude/skills/super-diagram/references/cli-export.md`

- [ ] **Step 1: Write cli-export.md**

Port bruce-drawio's CLI detection and export workflow:

**Detection order:**
1. `which draw.io 2>/dev/null || which drawio 2>/dev/null`
2. macOS: `/Applications/draw.io.app/Contents/MacOS/draw.io`
3. Windows: `"/c/Program Files/draw.io/draw.io.exe"` or `$LOCALAPPDATA/Programs/draw.io/draw.io.exe`
4. Linux: `/usr/bin/drawio` or `/snap/bin/drawio`

**Installation guidance (do NOT auto-install):**
| Platform | Command |
|----------|---------|
| macOS | `brew install --cask drawio` |
| Windows | `winget install JGraph.Draw` |
| Linux | `snap install drawio` |

**Export flags:**
| Flag | Purpose |
|------|---------|
| -x | Export mode (no GUI) |
| -f png/svg/pdf | Output format |
| -o path | Output path |
| --scale 2 | 2x resolution |
| --border 20 | Border padding (px) |
| --width 1600 | Constrain width |
| -p 0 | Export specific page |
| --crop | Crop to content |

**Example:**
```bash
"$DRAWIO" -x -f png --scale 2 -o output.png diagram.drawio
```

- [ ] **Step 2: Verify file**

Run: `test -f ~/.claude/skills/super-diagram/references/cli-export.md && echo "OK"`

---

### Task 11: Copy icons.md from fireworks

**Files:**
- Create: `~/.claude/skills/super-diagram/references/icons.md`

- [ ] **Step 1: Copy icons.md**

Copy fireworks' icons.md verbatim — it contains product color data that is format-agnostic. The SVG-specific rendering sections (cylinder templates, badge XML) should be kept as-is since they serve the SVG fallback mode, but add a note at the top:

```markdown
# Icon Reference

> **Note:** Product colors are format-agnostic and apply to both drawio (fillColor, strokeColor) and SVG modes. SVG rendering templates below are for the SVG fallback mode only.
```

Then append drawio-specific icon badge templates:

- **Product badge in drawio**: `ellipse;fillColor=#BRAND_COLOR;strokeColor=#BRAND_COLOR;strokeWidth=1;fontColor=#ffffff;fontSize=10;fontStyle=1;` with badge text as value
- **Vector DB in drawio**: `shape=cylinder3;size=10;fillColor=#FILL;strokeColor=#STROKE;` with product name as value

- [ ] **Step 2: Verify file**

Run: `grep -c 'fillColor' ~/.claude/skills/super-diagram/references/icons.md`
Expected: 10+ (drawio badge templates added)

---

### Task 12: End-to-end test — draw a diagram using the skill

**Files:**
- No new files — this is a validation step

- [ ] **Step 1: Start drawio MCP session**

Call `mcp__drawio__start_session` to open browser canvas.

- [ ] **Step 2: Generate a test diagram using the skill workflow**

Using the completed skill, generate a simple architecture diagram:
- Type: Architecture
- Style: 1 (Flat Icon)
- Content: Client → API Gateway → Order Service → PostgreSQL
- Follow the full 9-step workflow: classify → extract → complexity check → load references → map → layout → generate → visual review → export

This validates:
- SKILL.md workflow is followable
- style-1-flat-icon-drawio.md provides usable style strings
- shape-vocabulary-drawio.md provides correct mxCell XML
- arrow-semantics-drawio.md provides correct edge styles
- drawio-file-structure.md provides valid XML wrapper
- Visual review cycle works (export → Read → check → fix if needed)

- [ ] **Step 3: Verify diagram renders correctly in browser**

Check for: no crossing edges, no overlapping labels, correct colors, readable text.

- [ ] **Step 4: Export and verify**

Call `mcp__drawio__export_diagram` to export .drawio and .png files. Verify both exist.

---

### Task 13: Commit all files

**Files:**
- All files in `~/.claude/skills/super-diagram/`

- [ ] **Step 1: Verify complete file list**

Run: `find ~/.claude/skills/super-diagram -type f | sort`

Expected files:
```
~/.claude/skills/super-diagram/SKILL.md
~/.claude/skills/super-diagram/references/arrow-semantics-drawio.md
~/.claude/skills/super-diagram/references/cli-export.md
~/.claude/skills/super-diagram/references/diagram-templates-drawio.md
~/.claude/skills/super-diagram/references/drawio-file-structure.md
~/.claude/skills/super-diagram/references/icons.md
~/.claude/skills/super-diagram/references/shape-vocabulary-drawio.md
~/.claude/skills/super-diagram/references/style-1-flat-icon-drawio.md
~/.claude/skills/super-diagram/references/style-2-dark-terminal-drawio.md
~/.claude/skills/super-diagram/references/style-3-blueprint-drawio.md
~/.claude/skills/super-diagram/references/style-4-notion-clean-drawio.md
~/.claude/skills/super-diagram/references/style-5-glassmorphism-drawio.md
~/.claude/skills/super-diagram/references/style-6-claude-official-drawio.md
~/.claude/skills/super-diagram/references/style-7-openai-drawio.md
~/.claude/skills/super-diagram/references/style-diagram-matrix.md
~/.claude/skills/super-diagram/references/svg-output.md
```

Total: 16 files

- [ ] **Step 2: Commit**

```bash
cd ~/.claude/skills/super-diagram
git add -A
git commit -m "feat: add super-diagram skill — fused fireworks+drawio with mandatory visual review"
```

Note: If this directory is not a git repo, commit in the agent_project repo instead with the skill files referenced.

---

## Spec Coverage Check

| Spec Section | Task |
|-------------|------|
| 1. Overview | Task 1 (SKILL.md §1) |
| 2. When to Use | Task 1 (SKILL.md §2) |
| 3. Directory Structure | Task 1 |
| 4. SKILL.md Structure | Task 1 (all 11 sections) |
| 5. Workflow 9 Steps | Task 1 (SKILL.md §3) |
| 6. Sub-diagram Strategy | Task 1 (SKILL.md §4) |
| 7. Visual Review | Task 1 (SKILL.md §5) |
| 8. Editing Existing Diagrams | Task 1 (SKILL.md §6) |
| 9. Constraints | Task 1 (SKILL.md §9) |
| 10. Output Paths | Task 1 (SKILL.md §10) |
| 11. Style Mapping | Tasks 3-4 (7 style files) |
| 12. Shape Mapping | Task 6 |
| 13. Arrow Semantics | Task 7 |
| 14. Diagram Types | Task 8 |
| 15. Common Mistakes | Task 1 (SKILL.md §11) |
| drawio-file-structure | Task 2 |
| style-diagram-matrix | Task 5 |
| svg-output | Task 9 |
| cli-export | Task 10 |
| icons | Task 11 |
| End-to-end validation | Task 12 |
