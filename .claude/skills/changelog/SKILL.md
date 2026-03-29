---
name: changelog
description: Generate changelog entries from git commits. Use when asked to create or update a changelog, release notes, or summarize changes.
allowed-tools: Read, Write, Edit, Bash
argument-hint: [version or commit range]
---

# Changelog Generation Skill

Generate structured changelog entries from git history.

## How to Use

- `/changelog` - Generate from unreleased commits (since last tag)
- `/changelog v1.2.0` - Generate for specific version
- `/changelog v1.1.0..v1.2.0` - Generate for commit range
- `/changelog --since=2024-01-01` - Generate since date

## Process

### 1. Gather Commits

```bash
# Since last tag
git log $(git describe --tags --abbrev=0)..HEAD --oneline

# For a range
git log $ARGUMENTS --oneline

# With details
git log --pretty=format:"%h %s" $ARGUMENTS
```

### 2. Categorize Changes

Group commits into these categories:

| Category | Prefix in commits | Description |
|----------|-------------------|-------------|
| Added | feat:, add: | New features |
| Changed | change:, update:, refactor: | Changes to existing functionality |
| Fixed | fix:, bugfix: | Bug fixes |
| Removed | remove:, delete: | Removed features |
| Security | security: | Security fixes |
| Deprecated | deprecate: | Soon-to-be removed features |

### 3. Output Format

Use [Keep a Changelog](https://keepachangelog.com/) format:

```markdown
## [Version] - YYYY-MM-DD

### Added
- New feature description (#PR)

### Changed
- Change description (#PR)

### Fixed
- Bug fix description (#PR)

### Removed
- Removed feature description

### Security
- Security fix description
```

### 4. Update CHANGELOG File

If `CHANGELOG.md` exists:
1. Read existing content
2. Insert new version section after the header
3. Preserve existing entries

If no changelog exists:
1. Create `CHANGELOG.md` with header
2. Add the new version section

## Changelog Header Template

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

```

## Tips

- Write for users, not developers
- Focus on *what* changed, not *how*
- Link to PRs/issues when available
- Group related changes together
- Use active voice: "Add feature" not "Added feature" or "Feature was added"
