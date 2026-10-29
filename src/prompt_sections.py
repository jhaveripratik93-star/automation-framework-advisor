"""Section-wise parsing/assembly for the editable prompt files under
config/conversion_prompts/ and config/team_profiles/<id>/.

Problem this solves: the Settings -> Team Profiles editor used to show
an entire prompt file as one opaque text blob — hard to read, and
creating a brand-new file meant starting from nothing. This module:

  1. Splits an existing file into named sections on markdown `## `
     headings, so the editor can render one labeled, collapsible box
     per section instead of one giant one.
  2. Reassembles edited sections back into a file, preserving order.
  3. Provides a starter template (section names + guidance placeholder
     text) per artifact kind, modeled directly on the structure already
     used by the hand-written files in config/conversion_prompts/, so a
     brand-new syntax-rules/pair-rules/codegen-style file starts from a
     labeled scaffold instead of a blank box.

`## ` (markdown heading level 2) is the ONLY recognized section
delimiter — deliberately not a heuristic like "line is in ALL CAPS",
because several of these files contain ALL-CAPS content that is NOT a
heading (e.g. Robot Framework's own `*** Settings ***` / `*** Test
Cases ***` markers appear inside worked examples). A narrow, unambiguous
marker means splitting/rejoining never risks silently mangling example
code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "PromptSection", "split_sections", "join_sections",
    "has_sections", "section_template",
]

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class PromptSection:
    heading: str | None   # None for the "intro" text before the first heading
    body: str


def has_sections(text: str) -> bool:
    return bool(_HEADING_RE.search(text or ""))


def split_sections(text: str) -> list[PromptSection]:
    """Split on `## Heading` lines. Content before the first heading (if
    any non-whitespace content exists) becomes a heading=None section."""
    text = text or ""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [PromptSection(heading=None, body=text.strip())] if text.strip() else []

    sections: list[PromptSection] = []
    intro = text[: matches[0].start()].strip()
    if intro:
        sections.append(PromptSection(heading=None, body=intro))

    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(PromptSection(heading=heading, body=text[body_start:body_end].strip()))

    return sections


def join_sections(sections: list[PromptSection]) -> str:
    """Reassemble sections (in the given order) back into file text."""
    parts: list[str] = []
    for s in sections:
        body = (s.body or "").strip()
        if s.heading:
            parts.append(f"## {s.heading}\n\n{body}" if body else f"## {s.heading}")
        elif body:
            parts.append(body)
    return ("\n\n".join(parts) + "\n") if parts else ""


# ── Starter templates, one per artifact kind ───────────────────────────
# Each entry: (heading, placeholder guidance shown when creating a new
# file of this kind). Modeled on the real structure already used by
# config/conversion_prompts/* — new files start from the same shape.

_BASE_PROMPT_TEMPLATE: list[tuple[str, str]] = [
    ("Core Rules", (
        "1. Output ONLY valid, runnable {to_name} code — nothing else "
        "(no markdown fences, no explanations before/after).\n"
        "2. Preserve every assertion/check from the source as an "
        "equivalent assertion in the output.\n"
        "3. Every unit of test logic that performs a check MUST end up "
        "runnable (e.g. a real test case/function) in the output — no "
        "orphaned helpers with nothing calling them.\n"
        "4. Use {to_name}'s current stable API — no deprecated syntax.\n"
        "5. Do NOT output CI/CD pipeline YAML, GitHub Actions, or any "
        "deployment config — code conversion only."
    )),
]

_SYNTAX_RULES_TEMPLATE: list[tuple[str, str]] = [
    ("Output Rules (Strict)", (
        "1. Output ONLY valid, executable code in this framework.\n"
        "2. Do NOT wrap the output in markdown code fences.\n"
        "3. Do NOT include explanatory or conversational text."
    )),
    ("Technical Requirements", (
        "- Standard imports: <list the imports every converted file needs>\n"
        "- Preferred locator/selector strategy: <e.g. role-based > CSS > XPath>\n"
        "- Assertion style: <the one true way to assert in this framework>\n"
        "- Test structure: <function/class shape generated code must follow>"
    )),
    ("Correct Example", (
        "<Paste one small, complete, correct example file here — the "
        "single highest-leverage thing you can add. The model copies "
        "patterns from this far more reliably than from prose rules.>"
    )),
]

_PAIR_RULES_TEMPLATE: list[tuple[str, str]] = [
    ("Overview", "<One or two sentences: what does {from_name} map to in {to_name}, at a high level?>"),
    ("Required Setup", (
        "<Imports/libraries/fixtures every converted file needs, e.g.:\n"
        "    Library    RequestsLibrary>"
    )),
    ("Keyword / API Mapping", (
        "<A table of source-construct -> target-construct, one line each, e.g.:\n"
        "http.get(url)      -> GET On Session    alias    endpoint\n"
        "check(res, {...})  -> Status Should Be    200    ${response}>"
    )),
    ("Worked Example", (
        "<Paste one complete BEFORE ({from_name}) and AFTER ({to_name}) "
        "example. This is the single highest-leverage section — concrete "
        "examples transfer far more reliably than abstract rules.>"
    )),
]

_CODEGEN_STYLE_TEMPLATE: list[tuple[str, str]] = [
    ("Framework & Imports", "<e.g. Playwright TypeScript | @playwright/test | async/await>"),
    ("Locator Strategy", "<e.g. page.getByRole() > page.getByTestId() > page.locator()>"),
    ("Assertion Style", "<e.g. await expect(locator).toBeVisible() / toHaveText() / toHaveURL()>"),
    ("Structure Pattern", "<e.g. test.describe('Suite', () => { test('case', async ({ page }) => {...}) })>"),
]

_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "base_prompt": _BASE_PROMPT_TEMPLATE,
    "syntax_rules": _SYNTAX_RULES_TEMPLATE,
    "pair_rules": _PAIR_RULES_TEMPLATE,
    "codegen_style": _CODEGEN_STYLE_TEMPLATE,
}


def section_template(kind: str) -> list[PromptSection]:
    """Starter sections for a brand-new file of this artifact kind."""
    return [PromptSection(heading=h, body=b) for h, b in _TEMPLATES.get(kind, [])]


_LABEL_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z /]{2,30}):\s*(.+)$")


def migrate_label_lines(text: str) -> list[PromptSection]:
    """Convert the compact 'Label: value' line format used by
    agent_config.py's FRAMEWORK_CONTEXT (e.g. "Locators: ...",
    "Assertions: ...") into proper `## Label` sections.

    The first line of a FRAMEWORK_CONTEXT entry has no label (it's a
    one-line framework summary) — that becomes "## Framework & Imports".
    Lines that don't match the "Label: value" shape are appended to the
    previous section as a continuation line, so nothing is silently
    dropped even if a future entry doesn't perfectly follow the pattern.
    """
    lines = (text or "").splitlines()
    sections: list[PromptSection] = []
    for line in lines:
        line = line.rstrip()
        if not line.strip():
            continue
        m = _LABEL_LINE_RE.match(line)
        if m:
            sections.append(PromptSection(heading=m.group(1).strip(), body=m.group(2).strip()))
        elif sections:
            sections[-1].body = f"{sections[-1].body}\n{line.strip()}"
        else:
            sections.append(PromptSection(heading="Framework & Imports", body=line.strip()))
    return sections
