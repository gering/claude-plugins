---
description: Enforce /merge skill over direct gh pr merge
---

Never run `gh pr merge` directly. Always use the /merge skill instead — it runs preflight checks (CI status, reviews, blocking issues, mergeable state) that direct gh commands bypass. A PR whose branch is checked out in another worktree (a work-system lane) is merged from that worktree (`/land` or `/merge` there), never from the main checkout — `--delete-branch` pulls the branch out from under the session working there.
