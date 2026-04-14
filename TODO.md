# TODO

## work-system

- [ ] **Expand skill descriptions in Cloudflare style** (like `pr-flow` v1.0.4 and `knowledge-system` v1.1.1 did).
  - Rewrite the `description:` frontmatter of all 7 skills (`work-create`, `work-start`, `work-adopt`, `work-continue`, `work-check`, `work-close`, `work-list`) as multiline YAML with:
    - 2–4 sentences of concrete summary (what it does, key inputs/outputs)
    - A `Use when:` block listing English + German natural-language triggers for skill auto-discovery
  - Bump version to v1.1.7 (patch — description-only, no behavior change)
  - Reference commit `4e8e464` (pr-flow v1.0.4) for the exact pattern.
