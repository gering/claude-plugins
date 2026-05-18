---
description: Enforce /merge skill over direct gh pr merge
---

Never run `gh pr merge` directly. Always use the /merge skill instead — it runs preflight checks (CI status, reviews, blocking issues, mergeable state) that direct gh commands bypass.
