---
name: commit-and-push
description: Stage all changes, write a conventional commit message, commit, and push to the current remote branch. Use this after every task is completed.
---

# commit-and-push

After completing a task, follow these steps exactly:

## 1. Check what changed

```bash
git status
git diff --stat
```

## 2. Stage all relevant changes

```bash
git add -A
```

Do NOT stage files that likely contain secrets (`.env`, `credentials.json`, etc.).

## 3. Write a conventional commit message

Format: `<type>(<scope>): <short summary>`

Types:
- `feat` — new feature or capability
- `fix` — bug fix
- `refactor` — code restructuring without behaviour change
- `docs` — documentation only
- `chore` — maintenance, config, tooling

Scope: the scraper or module affected (e.g. `ramilevi`, `keshet`, `main`, `common`).

Summary: imperative mood, lowercase, no trailing period, max 72 chars.

Examples:
```
feat(keshet): rewrite to appId=4 per-branch/per-category endpoint
fix(ramilevi): handle multiplicative net_content values like '4*140'
chore(main): update stale branch examples in help text
```

## 4. Commit

```bash
git commit -m "<type>(<scope>): <summary>"
```

If the pre-commit hook modifies files, `git add -A && git commit --amend --no-edit` (only if the commit was just created in this session and has not been pushed).

## 5. Push

```bash
git push
```

If the branch has no upstream yet:
```bash
git push -u origin HEAD
```

## 6. Confirm

```bash
git log --oneline -3
```

Report the commit hash and message to the user.
