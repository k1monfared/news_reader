---
name: explain
description: Explain code with analogies, diagrams, and step-by-step walkthroughs. Use when asked to explain how code works, document complex logic, or onboard someone to a codebase.
allowed-tools: Read, Grep, Glob
argument-hint: [file, function, or concept]
---

# Code Explanation Skill

Explain code clearly using multiple techniques for understanding.

## How to Use

- `/explain auth.py` - Explain a file
- `/explain handleRequest` - Explain a function
- `/explain "the caching system"` - Explain a concept/subsystem

## Explanation Framework

### 1. Start with the Big Picture

Before diving into code, answer:
- **What** does this code do? (1 sentence)
- **Why** does it exist? (the problem it solves)
- **Where** does it fit in the system?

### 2. Use an Analogy

Connect to something familiar:

```
"This authentication middleware is like a bouncer at a club:
- Checks your ID (validates token)
- Looks you up on the list (verifies permissions)
- Either lets you in or turns you away (allow/deny)"
```

Good analogy sources:
- Physical world processes (factory, mail delivery, library)
- Social interactions (conversation, delegation)
- Games (rules, turns, scoring)

### 3. Draw a Diagram

Use ASCII diagrams for flow and structure:

```
Request Flow:

  Client ──► Middleware ──► Handler ──► Database
              │                            │
              │   (validates)              │
              ▼                            ▼
           [Token] ◄──────────────── [User Data]
```

```
Component Structure:

┌─────────────────────────────────┐
│           Application           │
├─────────────┬───────────────────┤
│   Router    │     Services      │
├─────────────┼───────────────────┤
│  Handlers   │   Repositories    │
└─────────────┴───────────────────┘
```

### 4. Walk Through Step-by-Step

Number the steps and trace the execution:

```
1. Request arrives at /api/users
2. Router matches to handleGetUsers()
3. Handler extracts query params
4. Repository.findAll() queries database
5. Results mapped to response format
6. JSON sent back to client
```

Include relevant line numbers: `auth.py:42`

### 5. Highlight Gotchas

Call out non-obvious behavior:

```
⚠️ Watch out:
- This function mutates the input array
- Exceptions here are caught silently
- Cache expires after 5 minutes (see config.py:12)
```

### 6. Provide Entry Points

For complex systems, suggest where to start:

```
Key files to understand this system:
1. config.py - All settings and constants
2. models.py - Data structures
3. handlers.py - Main logic entry points
```

## Output Structure

```
## [Name/Component]

**Purpose**: One-line description

**Analogy**: [Familiar comparison]

**How it works**:
[ASCII diagram]

**Step-by-step**:
1. First...
2. Then...

**Key files**:
- file.py:123 - Description

**Gotchas**:
- Warning about non-obvious behavior
```

## Tips

- Assume the reader is smart but unfamiliar with this specific code
- Explain *why*, not just *what*
- Use concrete examples over abstract descriptions
- Keep diagrams simple - clarity over completeness
- Link to source: `file.py:42` format
