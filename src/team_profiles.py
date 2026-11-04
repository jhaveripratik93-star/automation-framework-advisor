"""Team profiles — per-team configuration for conversion prompts and
codegen style rules.

Problem this solves: different teams have different coding standards,
architecture conventions, and design docs. Before this module, the tool
had exactly ONE shared configuration per deployment —
config/conversion_prompts/*.md for code conversion, and
src/codegen/agent_config.py's FRAMEWORK_CONTEXT dict for code
generation — so every team hitting the same deployment got identical
output, with no way to diverge.

A "team profile" is a directory under PROFILES_ROOT:

    config/team_profiles/<team_id>/
        profile.yaml                       # display_name, description
        conversion_prompts/
            base_system_prompt.md
            syntax_rules/<fw_slug>.md
            pair_rules/<from_slug>__to__<to_slug>.md
        codegen_style/
            <framework_key>.md             # overrides FRAMEWORK_CONTEXT[framework_key]

The built-in "default" profile is NOT a directory here — it's an alias
for the tool's original, already-documented configuration:
  - conversion prompts  -> config/conversion_prompts/ (unchanged path;
    anything already hand-edited there keeps working exactly as before)
  - codegen style        -> src/codegen/agent_config.py's FRAMEWORK_CONTEXT
    (unchanged; no file-based override needed to reproduce today's
    behaviour)

This keeps the feature purely additive: a deployment that never creates
a named profile behaves identically to before this module existed.
"""
from __future__ import annotations

import io
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_PROFILE_ID", "PROFILES_ROOT", "TeamProfile",
    "list_profiles", "get_profile", "profile_exists",
    "create_profile", "create_profile_from_repo", "delete_profile",
    "conversion_prompts_dir", "codegen_style_dir",
    "own_conversion_prompts_dir", "own_codegen_style_dir",
    "reference_docs_dir", "list_reference_docs", "add_reference_doc",
    "delete_reference_doc", "reference_docs_context",
    "export_profile_zip", "import_profile_zip",
    "read_text_artifact", "write_text_artifact", "slugify_id",
]

_ROOT = Path(__file__).resolve().parent.parent
PROFILES_ROOT = _ROOT / "config" / "team_profiles"
DEFAULT_CONVERSION_PROMPTS_DIR = _ROOT / "config" / "conversion_prompts"
DEFAULT_PROFILE_ID = "default"


@dataclass
class TeamProfile:
    id: str
    display_name: str
    description: str = ""
    is_default: bool = False


def slugify_id(name: str) -> str:
    """Human-entered profile name -> safe directory/id slug."""
    slug = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    return slug or "team"


def _manifest_path(team_id: str) -> Path:
    return PROFILES_ROOT / team_id / "profile.yaml"


def list_profiles() -> list[TeamProfile]:
    """All available profiles. 'default' is always first and always present."""
    profiles = [TeamProfile(
        id=DEFAULT_PROFILE_ID,
        display_name="Default (built-in)",
        description=(
            "The tool's original shared configuration — "
            "config/conversion_prompts/ and agent_config.py's "
            "FRAMEWORK_CONTEXT. Edit those directly, or create a named "
            "profile below to diverge from it for one team only."
        ),
        is_default=True,
    )]
    if PROFILES_ROOT.exists():
        for d in sorted(PROFILES_ROOT.iterdir()):
            if not d.is_dir():
                continue
            display_name, description = d.name, ""
            manifest = _manifest_path(d.name)
            if manifest.is_file():
                try:
                    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
                    display_name = data.get("display_name", d.name)
                    description = data.get("description", "")
                except Exception as exc:
                    logger.warning("team_profiles: failed to read %s: %s", manifest, exc)
            profiles.append(TeamProfile(id=d.name, display_name=display_name, description=description))
    return profiles


def get_profile(team_id: str) -> TeamProfile | None:
    for p in list_profiles():
        if p.id == team_id:
            return p
    return None


def profile_exists(team_id: str) -> bool:
    return get_profile(team_id) is not None


def conversion_prompts_dir(team_id: str) -> Path:
    """Directory load_prompt_profile() should read from for this team.

    Falls back to the shared default directory if the team has no
    conversion_prompts/ of its own (e.g. it only overrides codegen style).
    """
    if team_id and team_id != DEFAULT_PROFILE_ID:
        team_dir = PROFILES_ROOT / team_id / "conversion_prompts"
        if team_dir.exists():
            return team_dir
    return DEFAULT_CONVERSION_PROMPTS_DIR


def codegen_style_dir(team_id: str) -> Path | None:
    """Directory get_framework_context() should check for this team, or
    None for 'default' / a team with no on-disk override (falls back to
    agent_config.FRAMEWORK_CONTEXT directly)."""
    if team_id and team_id != DEFAULT_PROFILE_ID:
        team_dir = PROFILES_ROOT / team_id / "codegen_style"
        if team_dir.exists():
            return team_dir
    return None


def own_conversion_prompts_dir(team_id: str) -> Path:
    """This team's OWN conversion_prompts/ directory — no fallback.

    `conversion_prompts_dir()` deliberately falls back to the shared
    default directory when a team has no override, which is correct for
    READING at conversion time (a team with no custom prompt should use
    the default). It is NOT correct for WRITING: a save must always land
    in the team's own directory, never in config/conversion_prompts/
    (the shared default every profile reads from). Writing through the
    read-fallback resolver is exactly what made edits to one profile
    leak into all of them. Edit UIs resolve write paths through here.
    """
    if team_id == DEFAULT_PROFILE_ID:
        return DEFAULT_CONVERSION_PROMPTS_DIR
    return PROFILES_ROOT / team_id / "conversion_prompts"


def own_codegen_style_dir(team_id: str) -> Path:
    """This team's OWN codegen_style/ directory — no fallback.

    Same rationale as `own_conversion_prompts_dir`: the read-time
    resolver (`codegen_style_dir`) returns None when a team has no
    override so callers fall back to the built-in FRAMEWORK_CONTEXT, but
    a WRITE must always target the team's own directory. Only valid for
    named profiles — 'default' has no on-disk codegen style to write to.
    """
    if team_id == DEFAULT_PROFILE_ID:
        raise ValueError("The default profile's codegen style is in code, not a file.")
    return PROFILES_ROOT / team_id / "codegen_style"


def reference_docs_dir(team_id: str) -> Path | None:
    """Directory holding this team's uploaded reference/architecture/
    design documents, or None for 'default' — reference documents are a
    per-team concept with no built-in equivalent to fall back to (unlike
    conversion prompts / codegen style, which mirror existing shared
    config)."""
    if team_id and team_id != DEFAULT_PROFILE_ID:
        return PROFILES_ROOT / team_id / "reference_docs"
    return None


def list_reference_docs(team_id: str) -> list[Path]:
    """All reference documents attached to this team, sorted by name."""
    d = reference_docs_dir(team_id)
    if d is None or not d.exists():
        return []
    return sorted(p for p in d.iterdir() if p.is_file())


def add_reference_doc(team_id: str, filename: str, raw_bytes: bytes) -> Path:
    """Extract text from an uploaded document and store it for this team.

    Raises ValueError for 'default' (no on-disk location to store it) or
    an unsupported file type.
    """
    from src.doc_extraction import SUPPORTED_EXTENSIONS, extract_text

    d = reference_docs_dir(team_id)
    if d is None:
        raise ValueError(
            "The default profile has no reference-documents location — "
            "create a named profile first."
        )
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext or filename}'. Supported: "
            f"{', '.join(SUPPORTED_EXTENSIONS)}."
        )

    text = extract_text(filename, raw_bytes)
    if not text.strip():
        raise ValueError(f"Could not extract any text from '{filename}'.")

    stem = slugify_id(filename.rsplit(".", 1)[0])
    out_path = d / f"{stem}.md"
    write_text_artifact(out_path, f"<!-- source: {filename} -->\n\n{text}")
    logger.info("team_profiles: added reference doc '%s' to '%s'", filename, team_id)
    return out_path


def delete_reference_doc(team_id: str, filename: str) -> None:
    d = reference_docs_dir(team_id)
    if d is None:
        raise ValueError("'default' has no reference documents to delete.")
    path = d / filename
    if not path.is_file():
        raise ValueError(f"Reference document '{filename}' not found.")
    path.unlink()
    logger.info("team_profiles: deleted reference doc '%s' from '%s'", filename, team_id)


def reference_docs_context(team_id: str) -> str:
    """Concatenated text of every reference document attached to this
    team, ready to append to a system prompt. Empty string for 'default'
    or a team with none attached."""
    docs = list_reference_docs(team_id)
    if not docs:
        return ""
    parts = []
    for p in docs:
        parts.append(f"### {p.stem}\n\n{read_text_artifact(p)}")
    return "\n\n".join(parts)


def read_text_artifact(path: Path) -> str:
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("team_profiles: failed to read %s: %s", path, exc)
    return ""


def write_text_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_profile(
    team_id: str,
    display_name: str,
    description: str = "",
    clone_from: str = DEFAULT_PROFILE_ID,
) -> TeamProfile:
    """Create a new named profile, seeded from an existing one (default,
    unless told otherwise) so a team starts from a full editable copy of
    today's behaviour instead of blank files.
    """
    team_id = slugify_id(team_id)
    if team_id == DEFAULT_PROFILE_ID:
        raise ValueError("'default' is reserved — choose another name.")
    team_root = PROFILES_ROOT / team_id
    if team_root.exists():
        raise ValueError(f"A profile with id '{team_id}' already exists.")

    team_root.mkdir(parents=True, exist_ok=True)

    write_text_artifact(
        team_root / "profile.yaml",
        yaml.safe_dump(
            {"display_name": display_name or team_id, "description": description},
            sort_keys=False,
        ),
    )

    # Seed conversion_prompts/ from the clone source.
    src_prompts_dir = conversion_prompts_dir(clone_from)
    if src_prompts_dir.exists():
        shutil.copytree(src_prompts_dir, team_root / "conversion_prompts")

    # Seed codegen_style/ — copy the clone source's on-disk overrides if
    # it has any, otherwise materialise the current
    # agent_config.FRAMEWORK_CONTEXT dict into editable files so the new
    # team starts from a full copy of today's defaults, not a blank slate.
    dst_style_dir = team_root / "codegen_style"
    src_style_dir = codegen_style_dir(clone_from)
    if src_style_dir is not None and src_style_dir.exists():
        shutil.copytree(src_style_dir, dst_style_dir)
    else:
        from src.codegen.agent_config import FRAMEWORK_CONTEXT
        from src.prompt_sections import join_sections, migrate_label_lines

        dst_style_dir.mkdir(parents=True, exist_ok=True)
        for fw_key, text in FRAMEWORK_CONTEXT.items():
            # Materialise the compact "Label: value" format into proper
            # `## Label` sections, so the new profile's files are
            # immediately readable/editable section-by-section in the
            # Settings UI rather than one unlabelled blob.
            sectioned = join_sections(migrate_label_lines(text))
            write_text_artifact(dst_style_dir / f"{fw_key}.md", sectioned)

    logger.info("team_profiles: created '%s' (cloned from '%s')", team_id, clone_from)
    return TeamProfile(id=team_id, display_name=display_name or team_id, description=description)


# Maps RepoScanner's `detected_test_framework` values to the codegen
# style keys used by agent_config.FRAMEWORK_CONTEXT. Best-effort: a repo
# using "selenium" could be selenium_py OR selenium_java — this picks
# the Python variant and clearly labels the result as detected/editable,
# so a wrong guess is a quick edit, not a silent wrong assumption baked
# in permanently.
_DETECTED_FW_TO_STYLE_KEY: dict[str, str] = {
    "pytest-playwright": "playwright_py",
    "playwright": "playwright_ts",
    "selenium": "selenium_py",
    "testng": "selenium_java",
    "junit": "selenium_java",
    "cypress": "cypress_js",
    "robot-framework": "robot_framework",
    "rest-assured": "rest_assured",
}


def create_profile_from_repo(
    team_id: str,
    display_name: str,
    repo_source: str,
    description: str = "",
    base_profile: str = DEFAULT_PROFILE_ID,
) -> tuple[TeamProfile, str]:
    """Create a new profile seeded from `base_profile` (as `create_profile`
    does), THEN scan an existing repo and append what was detected —
    test framework, base URLs, env var naming, existing fixture/helper/
    page-object naming conventions — as a clearly-labelled "Detected From
    Your Repo" section onto that framework's codegen style file.

    This is deliberately a DRAFT, not a finished profile: the detected
    section is appended (not replacing anything), under its own heading,
    so it's obviously provisional and trivially reviewed or deleted via
    the normal section editor — a static scan can get naming conventions
    right but cannot infer a team's actual intent, so a human should
    still read it over before relying on it.

    Returns (the created TeamProfile, a human-readable report of what
    was detected and where it was applied).
    """
    from src.codegen.repo_scanner import scan as scan_repo

    profile = create_profile(team_id, display_name, description, clone_from=base_profile)
    team_id = profile.id

    try:
        ctx = scan_repo(repo_source)
    except Exception as exc:
        report = (
            f"Profile '{profile.display_name}' created from '{base_profile}', but "
            f"scanning '{repo_source}' failed: {exc}\n"
            f"The profile still exists with {base_profile}'s defaults — fix the "
            f"repo source and use 'Learn from repo' again, or edit sections manually."
        )
        return profile, report

    detected_fw = ctx.get("detected_test_framework", "")
    style_key = _DETECTED_FW_TO_STYLE_KEY.get(detected_fw)

    report_lines = [f"Scanned '{repo_source}':", ctx.get("summary", "(no summary)")]

    if not style_key:
        report_lines.append(
            "\nCould not confidently map the detected test framework to a "
            "codegen style key — no automatic edits made. Review "
            "`✏️ Edit profile content` and fill in conventions manually."
        )
        return profile, "\n".join(report_lines)

    style_dir = codegen_style_dir(team_id)
    style_path = style_dir / f"{style_key}.md"  # type: ignore[union-attr]
    existing = read_text_artifact(style_path)

    detected_notes: list[str] = [f"Detected test framework: {detected_fw}"]
    if ctx.get("base_urls"):
        detected_notes.append("Base URL(s) seen in this repo: " + ", ".join(ctx["base_urls"][:3]))
    if ctx.get("env_vars"):
        detected_notes.append("Env vars used: " + ", ".join(ctx["env_vars"][:10]))
    if ctx.get("existing_fixtures"):
        detected_notes.append("Existing fixtures: " + ", ".join(
            f.split("::")[-1] for f in ctx["existing_fixtures"][:8]
        ))
    if ctx.get("existing_helpers"):
        detected_notes.append("Existing helpers/utils: " + ", ".join(
            h.split("::")[-1] for h in ctx["existing_helpers"][:8]
        ))
    if ctx.get("existing_page_objects"):
        detected_notes.append("Existing page objects: " + ", ".join(
            p.split("::")[-1] for p in ctx["existing_page_objects"][:8]
        ))
    if ctx.get("external_deps"):
        detected_notes.append("External systems integrated: " + ", ".join(ctx["external_deps"]))

    new_section = (
        "\n\n## Detected From Your Repo (draft — review before relying on this)\n\n"
        + "\n".join(f"- {n}" for n in detected_notes)
    )
    write_text_artifact(style_path, (existing + new_section) if existing else new_section.strip())

    report_lines.append(f"\nApplied as a new section in codegen style for '{style_key}'.")
    report_lines.append(f"Review it under Edit profile content -> Codegen — framework style rules -> {style_key}.")
    return profile, "\n".join(report_lines)


def delete_profile(team_id: str) -> None:
    if team_id == DEFAULT_PROFILE_ID:
        raise ValueError("Cannot delete the default profile.")
    team_root = PROFILES_ROOT / team_id
    if not team_root.exists():
        raise ValueError(f"Profile '{team_id}' does not exist.")
    shutil.rmtree(team_root)
    logger.info("team_profiles: deleted '%s'", team_id)


def export_profile_zip(team_id: str) -> bytes:
    """Zip a team profile's entire directory (profile.yaml,
    conversion_prompts/, codegen_style/, reference_docs/) into bytes —
    the portable unit for sharing a profile with another person or
    machine. Pair with `import_profile_zip` on the receiving end.
    """
    if team_id == DEFAULT_PROFILE_ID:
        raise ValueError("The default profile has no directory to export — it's built in.")
    team_root = PROFILES_ROOT / team_id
    if not team_root.is_dir():
        raise ValueError(f"Profile '{team_id}' does not exist.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(team_root.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(team_root)))
    logger.info("team_profiles: exported '%s' to zip", team_id)
    return buf.getvalue()


def import_profile_zip(
    zip_bytes: bytes,
    team_id: str | None = None,
    display_name: str | None = None,
    overwrite: bool = False,
) -> TeamProfile:
    """Import a profile previously produced by `export_profile_zip`.

    `team_id` overrides the id embedded in the zip's profile.yaml — lets
    the importer rename it, or avoid a collision with a profile they
    already have. Raises ValueError if: the archive isn't a valid zip,
    it doesn't look like an exported profile (no profile.yaml), the
    target id is already in use and `overwrite` is False, or any entry's
    path would extract outside the target directory (a "zip slip" guard
    — never trust an archive's internal paths without checking, even one
    a colleague sent you in good faith).
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Not a valid zip file: {exc}") from exc

    names = zf.namelist()
    if "profile.yaml" not in names:
        raise ValueError("This doesn't look like an exported profile — missing profile.yaml.")

    try:
        manifest = yaml.safe_load(zf.read("profile.yaml").decode("utf-8")) or {}
    except Exception as exc:
        raise ValueError(f"Could not read profile.yaml from the archive: {exc}") from exc

    resolved_id = slugify_id(team_id) if team_id else slugify_id(manifest.get("display_name", "imported"))
    if resolved_id == DEFAULT_PROFILE_ID:
        raise ValueError("'default' is reserved — choose another name.")

    target_root = PROFILES_ROOT / resolved_id
    if target_root.exists() and not overwrite:
        raise ValueError(
            f"A profile with id '{resolved_id}' already exists. Choose a "
            f"different name, or confirm overwrite to replace it."
        )

    # Zip-slip guard: every member must resolve to a path INSIDE target_root.
    target_root.parent.mkdir(parents=True, exist_ok=True)
    target_root_resolved = (target_root.parent / resolved_id).resolve()
    for member in names:
        member_resolved = (target_root_resolved / member).resolve()
        try:
            member_resolved.relative_to(target_root_resolved)
        except ValueError:
            raise ValueError(f"Refusing to import: unsafe path in archive ('{member}').") from None

    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    zf.extractall(target_root)

    final_display_name = display_name or manifest.get("display_name", resolved_id)
    final_description = manifest.get("description", "")
    write_text_artifact(
        target_root / "profile.yaml",
        yaml.safe_dump(
            {"display_name": final_display_name, "description": final_description},
            sort_keys=False,
        ),
    )

    logger.info("team_profiles: imported '%s' from zip (overwrite=%s)", resolved_id, overwrite)
    return TeamProfile(id=resolved_id, display_name=final_display_name, description=final_description)
