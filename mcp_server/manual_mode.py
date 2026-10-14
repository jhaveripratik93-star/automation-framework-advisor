"""Manual-mode brief builders for the LLM-backed MCP tools.

GROQ_API_KEY is optional for this server. When it's not configured, the
conversion/generation tools below don't fail — they switch to "manual
mode": they reuse the exact same framework rules, syntax guides, and
capability-gap analysis that would otherwise be sent to Groq, and hand
them back as a structured brief. The calling assistant (Kiro's own
model, already in the conversation) then produces the converted/
generated code directly using that brief — no external LLM API call
needed.

This deliberately reuses `ToolExecutor`'s prompt-building helpers
(`_build_system_prompt`, `_resolve_target_language`, `_build_gap_notes`,
`_resolve_framework`) and `src.codegen.agent_config`'s per-framework
context — those are the single source of truth for conversion rules
used by the Streamlit app too, so manual mode produces output governed
by identical rules, just executed by a different model.
"""
from __future__ import annotations

from typing import Any


def build_conversion_brief(
    executor: Any,
    source_code: str,
    from_framework: str,
    to_framework: str,
) -> dict[str, Any]:
    """Build a manual-mode brief for converting one test file.

    Mirrors `ToolExecutor._convert_test_cases` up to the point where it
    would call the LLM, then returns the assembled system prompt instead
    of sending it anywhere.
    """
    from_fw = executor._resolve_framework(from_framework)
    to_fw = executor._resolve_framework(to_framework)
    from_name = from_fw.framework_name if from_fw else from_framework
    to_name = to_fw.framework_name if to_fw else to_framework
    target_lang, eco = executor._resolve_target_language(to_fw, to_name)
    gap_notes, gaps = executor._build_gap_notes(from_fw, to_fw, to_name, target_lang)
    system_prompt = executor._build_system_prompt(
        from_name, to_name, gap_notes, target_lang=target_lang, to_fw=to_fw
    )

    return {
        "mode": "manual",
        "from_framework": from_name,
        "to_framework": to_name,
        "target_language": target_lang,
        "capability_gaps": gaps,
        "conversion_rules": system_prompt,
        "source_code": source_code,
        "instructions": (
            f"GROQ_API_KEY is not configured, so this tool did not call an "
            f"external LLM. Using `conversion_rules` as the authoritative "
            f"rule set, convert `source_code` from {from_name} to {to_name} "
            f"yourself and present the complete converted file to the user "
            f"(and write it to disk if they have an open workspace for it). "
            + (
                f"Note the capability gaps in `capability_gaps` — generate a "
                f"small standalone helper for each, as `conversion_rules` "
                f"describes."
                if gaps else
                "There are no capability gaps between these frameworks for "
                "this source."
            )
            + " IMPORTANT: the converted file must be runnable on its own — "
            "if `source_code` performs any assertions/checks, the converted "
            "file MUST contain an actual test entry that calls them (e.g. a "
            "Robot Framework '*** Test Cases ***' case, a pytest `def "
            "test_*`, a `test()`/`it()` block), not just keyword/function "
            "*definitions* with nothing invoking them. Do not emit a file "
            "that only defines keywords/helpers unless the source file "
            "itself was purely a library with no top-level invocation."
        ),
    }


def build_multi_file_conversion_brief(
    executor: Any,
    files: list[dict[str, str]],
    from_framework: str,
    to_framework: str,
) -> dict[str, Any]:
    """Build a manual-mode brief for converting a multi-file test project.

    Deterministic artifacts (conftest.py, requirements.txt) don't need an
    LLM — those are generated for real, same as the Groq-backed path. Only
    the per-file code conversion is handed to the calling assistant.
    """
    from_fw = executor._resolve_framework(from_framework)
    to_fw = executor._resolve_framework(to_framework)
    from_name = from_fw.framework_name if from_fw else from_framework
    to_name = to_fw.framework_name if to_fw else to_framework
    target_lang, eco = executor._resolve_target_language(to_fw, to_name)
    gap_notes, gaps = executor._build_gap_notes(from_fw, to_fw, to_name, target_lang)

    shared_context = "\n\n".join(
        f"# --- {f['filename']} ---\n" + "\n".join(f["content"].splitlines()[:100])
        for f in files
    )

    # Ingest the imported library/helper layer via the SHARED executor method, so
    # the no-key manual path derives the identical auth/transport contract and
    # report as the Groq-backed `convert_multi_file`.
    lib_ctx = executor._build_library_context(files, from_name, to_name, target_lang)

    system_prompt = executor._build_system_prompt(
        from_name, to_name, gap_notes, shared_context, target_lang, to_fw=to_fw,
        library_block=lib_ctx.prompt_block,
    )

    # These two are deterministic (no LLM involved even in the Groq path).
    conftest = (
        executor._generate_conftest(to_name, gaps, [])
        if target_lang == "python" else ""
    )
    requirements = (
        executor._generate_requirements(to_name, gaps, from_name)
        if target_lang == "python" else ""
    )

    helper_note = ""
    if lib_ctx.helper_spec is not None:
        helper_note = (
            f" A shared auth helper ({lib_ctx.helper_spec.path}, exporting "
            f"{', '.join(lib_ctx.helper_spec.exports)}) was derived from the "
            f"imported library layer — write it and import it from each converted "
            f"file instead of reinventing inline login."
        )

    # Build the same generic completeness report the Groq path emits (shared
    # executor logic). `converted` isn't known yet in manual mode, so report the
    # count of files the assistant will convert.
    report = executor._build_conversion_report(
        lib_ctx,
        from_name=from_name,
        to_name=to_name,
        target_lang=target_lang,
        converted={f["filename"]: "" for f in files},
        helpers=({lib_ctx.helper_spec.path: lib_ctx.helper_spec.content}
                 if lib_ctx.helper_spec is not None else {}),
        conftest=conftest,
        requirements=requirements,
        gaps=gaps,
        assertion_report=None,
    )

    brief: dict[str, Any] = {
        "mode": "manual",
        "from_framework": from_name,
        "to_framework": to_name,
        "target_language": target_lang,
        "capability_gaps": gaps,
        "conversion_rules": system_prompt,
        "conftest": conftest,
        "requirements": requirements,
        "files_to_convert": files,
        "report": report,
        "instructions": (
            f"GROQ_API_KEY is not configured, so this tool did not call an "
            f"external LLM. `conftest` and `requirements` were still "
            f"generated for real (deterministic). Using `conversion_rules` "
            f"as the authoritative rule set and `files_to_convert` as the "
            f"source (filenames are preserved except the extension, which "
            f"changes to match {to_name}), convert every file yourself and "
            f"present/write the complete converted project to the user. "
            + (
                f"Generate a small standalone helper for each entry in "
                f"`capability_gaps`, as `conversion_rules` describes."
                if gaps else
                "There are no capability gaps between these frameworks for "
                "this source."
            )
            + helper_note
            + " See `report` for a structured breakdown of what was handled, "
            "assumed, and not done; relay it to the user."
        ),
    }

    # Additive, optional: the shared auth helper to write out.
    if lib_ctx.helper_spec is not None:
        brief["auth_helper"] = {
            "path": lib_ctx.helper_spec.path,
            "content": lib_ctx.helper_spec.content,
            "exports": list(lib_ctx.helper_spec.exports),
        }

    return brief


def build_codegen_brief(
    target_framework: str,
    test_cases: list[dict[str, Any]],
    selector_map: dict[str, str] | None,
) -> dict[str, Any]:
    """Build a manual-mode brief for generating test code from manual test
    cases, when no LLM is configured.

    Reuses the same per-framework context (`FRAMEWORK_CONTEXT`) and step
    generation rules (`STEP_GENERATOR_SYSTEM`) that `CodeGenOrchestrator`
    sends to Groq, so manual-mode output follows identical conventions.
    """
    from src.codegen.agent_config import FRAMEWORK_CONTEXT, STEP_GENERATOR_SYSTEM

    system_prompt = STEP_GENERATOR_SYSTEM.get(
        target_framework, STEP_GENERATOR_SYSTEM.get("playwright_ts", "")
    )
    framework_ctx = FRAMEWORK_CONTEXT.get(target_framework, "")
    if framework_ctx:
        system_prompt = f"{system_prompt}\n\nFramework reference:\n{framework_ctx}"

    return {
        "mode": "manual",
        "target_framework": target_framework,
        "generation_rules": system_prompt,
        "selector_map": selector_map or {},
        "test_cases": test_cases,
        "instructions": (
            f"GROQ_API_KEY is not configured, so this tool did not call an "
            f"external LLM. Using `generation_rules` as the authoritative "
            f"rule set, generate complete {target_framework} test code for "
            f"every entry in `test_cases` yourself. Use entries in "
            f"`selector_map` for the elements they name; for anything not "
            f"in `selector_map`, invent a reasonable selector and flag it "
            f"to the user for verification. Present the generated file(s) "
            f"to the user (and write them to disk if they have an open "
            f"workspace for it)."
        ),
    }
