---
name: loglog
description: Use when creating documentation, notes, status files, or any .log files. Loglog is a hierarchical plain-text documentation format that converts to markdown, HTML, LaTeX, and PDF.
allowed-tools: Read, Write, Edit, Bash
---

# Loglog Documentation Format

When creating documentation, notes, or status tracking files, use the loglog format with `.log` extension.

## Core Syntax

### Hierarchical Lists
Everything is a list. Use dashes (`-`) with 4-space indentation per level:

```
- Item at depth 0
    - Item at depth 1
        - Item at depth 2
    - Another depth 1 item
- Back to depth 0
```

### TODO Markers
Track tasks with bracket notation at the start of items:

```
- [] Pending task
- [x] Completed task
- [?] Unknown status
```

### Hashtags for Filtering
Tag content for organization and extraction:

```
- Important decision #decision #important
- Meeting notes #meeting
```

## File Naming
- Use `.log` extension: `STATUS.log`, `notes.log`, `project.log`
- Keep names lowercase and descriptive

## Conversion Commands

```bash
# Convert to markdown (most common)
loglog convert file.log --to markdown
loglog file.log > file.md          # shorthand

# Other formats
loglog convert file.log --to html   # Interactive HTML
loglog convert file.log --to latex
loglog convert file.log --to pdf
```

## Python API

```python
from loglog import to_md_file, to_html_file

to_md_file('notes.log')      # Creates notes.md
to_html_file('notes.log')    # Creates interactive HTML
```

## Example STATUS.log

```
- Project Status
    - Name: My Project
    - Stage: MVP
    - Last Updated: 2024-01-15
    - Development Mode: Claude Code

- Current Sprint #sprint
    - [] Implement user authentication
    - [x] Set up database schema
    - [?] Review API design

- Notes #notes
    - Decision: Using PostgreSQL for persistence #decision
    - Next milestone: Beta release
```

## Key Principles

1. **Everything is a list** - Even list items contain lists
2. **Plain text first** - Write content, organize structure later via indentation
3. **Foldable** - Most editors support collapsing nested sections
4. **Convertible** - Transform to markdown/HTML/PDF when needed

## Installation

```bash
pip install loglog
# or
cargo install loglog
```

When asked to create documentation, status files, or notes in this project, default to loglog format unless the user specifies otherwise.
