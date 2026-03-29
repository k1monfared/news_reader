---
name: review
description: Review code changes for quality, security, performance, and style issues. Use when asked to review code, PRs, or diffs.
allowed-tools: Read, Bash, Grep, Glob
argument-hint: [file or PR number]
---

# Code Review Skill

When reviewing code, follow this systematic checklist.

## How to Use

- `/review` - Review staged changes (git diff --cached)
- `/review file.py` - Review specific file
- `/review 123` - Review PR #123 (requires gh CLI)

## Review Process

### 1. Gather Context
First, understand what changed:
```bash
# For staged changes
git diff --cached

# For PR
gh pr diff $ARGUMENTS
```

### 2. Review Checklist

#### Security
- [ ] No hardcoded secrets, API keys, or credentials
- [ ] Input validation on user-provided data
- [ ] No SQL injection vulnerabilities (parameterized queries)
- [ ] No XSS vulnerabilities (escaped output)
- [ ] No command injection (sanitized shell inputs)
- [ ] Proper authentication/authorization checks

#### Logic & Correctness
- [ ] Code does what it's supposed to do
- [ ] Edge cases handled (null, empty, boundary values)
- [ ] Error handling is appropriate
- [ ] No obvious bugs or typos
- [ ] No race conditions or deadlocks

#### Performance
- [ ] No N+1 queries or unnecessary loops
- [ ] Efficient algorithms for data size
- [ ] No memory leaks (cleanup in finally/defer)
- [ ] Appropriate caching if needed

#### Code Quality
- [ ] Follows project conventions
- [ ] Clear naming (variables, functions)
- [ ] No dead code or commented-out code
- [ ] DRY - no unnecessary duplication
- [ ] Functions are focused (single responsibility)

#### Testing
- [ ] New code has appropriate tests
- [ ] Tests cover happy path and edge cases
- [ ] Existing tests still pass

### 3. Output Format

Provide findings in this format:

```
## Review Summary

**Files reviewed**: [list]
**Risk level**: [Low/Medium/High]

### Issues Found

#### Critical
- [file:line] Description of critical issue

#### Suggestions
- [file:line] Suggested improvement

### What's Good
- Positive observations

### Verdict
[ ] Approve
[ ] Request changes
[ ] Needs discussion
```

## Notes

- Be constructive, not critical
- Explain *why* something is an issue
- Suggest fixes, don't just point out problems
- Acknowledge good patterns when you see them
