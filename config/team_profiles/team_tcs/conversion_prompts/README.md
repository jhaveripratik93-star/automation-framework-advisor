# Conversion prompt configuration

This directory holds the **editable** prompt text used by the code
conversion feature (`src/conversion/`). Nothing here is hardcoded in
Python — edit these files directly to change how the LLM converts code,
and every teammate using this tool (Streamlit UI or the MCP server) picks
up the change immediately, with no code deploy.

## Layout

```
conversion_prompts/
├── base_system_prompt.md          # Shared framing + rules every conversion gets
├── syntax_rules/
│   ├── robot_framework.md         # Target-framework-specific syntax rules
│   ├── playwright.md
│   ├── selenium.md
│   └── k6.md
└── pair_rules/
    └── k6__to__robot_framework.md # Rules specific to converting FROM k6 TO Robot Framework
```

## Rules

- `base_system_prompt.md` is always included. It must NOT mention CI/CD,
  GitHub Actions, or pipeline YAML — code conversion only ever outputs
  test code. If you add instructions here, keep that boundary.
- `syntax_rules/<framework_slug>.md` is included when converting **to**
  that framework. The slug is the framework's display name, lowercased,
  with spaces replaced by underscores (e.g. "Robot Framework" →
  `robot_framework.md`).
- `pair_rules/<from_slug>__to__<to_slug>.md` is included (in addition to
  the syntax rules) when converting from one specific framework to
  another. Pair rules should contain conversion mappings and worked
  examples specific to that pair (e.g. k6 `check()` → Robot Framework
  `Status Should Be`). If no pair file exists, only the generic syntax
  rules apply.
- Add a new framework by dropping in a new `syntax_rules/<slug>.md` file —
  no code changes required.

## Validation

Every file here is loaded as plain text and concatenated into the LLM
system prompt. There's no templating syntax — just write the rules in
plain English/Markdown, the way you'd describe them to a new team member
converting code by hand.
