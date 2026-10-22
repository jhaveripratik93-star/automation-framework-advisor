"""PrerequisitesExecutor — small feature-owned dispatcher for the
"analyze manual test prerequisites" tool.

Extracted from the former monolithic ``src.tools.executor.ToolExecutor``.
Owns exactly ``analyze_prerequisites``: matching prerequisite steps to
automation patterns, generating script skeletons, CI/CD pipeline YAML,
and framework-specific setup hooks.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from src.prerequisites.cicd_generators import generate_cicd_pipeline, generate_framework_hooks
from src.prerequisites.patterns import generate_script, match_prereq_pattern
from src.tools.base_executor import BaseToolExecutor

if TYPE_CHECKING:
    from src.knowledge_base.loader import KnowledgeBase

logger = logging.getLogger(__name__)


class PrerequisitesExecutor(BaseToolExecutor):
    """Executes prerequisite-automation tool calls by name."""

    def __init__(self, knowledge_base: KnowledgeBase | None = None) -> None:
        super().__init__(knowledge_base)

        self.tools: dict[str, Callable] = {
            "analyze_prerequisites": self._analyze_prerequisites,
        }

    def _analyze_prerequisites(
        self, prerequisites: list[dict], target_framework: str | None = None
    ) -> str:
        """Analyze pre-requisites; suggest automation scripts and CI/CD integration.

        Workflow:
          1. Resolve optional target framework.
          2. Match each prerequisite to an automation pattern.
          3. Emit script skeleton + CI/CD stage for each.
          4. Generate full CI/CD pipeline YAML.
          5. Generate framework-specific hooks (conftest.py, playwright config, etc.).
        """
        from src.tools.formatters import resolve_framework

        lines = ["# Pre-requisite Automation Analysis\n"]

        fw = resolve_framework(self.kb, target_framework) if target_framework else None
        fw_cicd: dict = {}
        if fw:
            fw_cicd = {k: v for k, v in fw.cicd_integration.items() if v and v is not False}
            lines.append(f"**Target Framework:** {fw.framework_name}\n")

        automation_plan: list[dict] = []

        for prereq in prerequisites:
            name = prereq.get("name", "Unknown")
            desc = prereq.get("description", "")
            manual = prereq.get("manual_steps", "")

            matched_pattern = match_prereq_pattern(prereq)

            lines.append(f"## {name}\n")
            lines.append(f"**Description:** {desc or 'N/A'}")
            if manual:
                lines.append(f"**Current Manual Steps:** {manual}\n")

            if matched_pattern:
                pname, p = matched_pattern
                lines += [
                    f"**✅ Can be Automated:** Yes ({p['automation'].upper()} script)\n",
                    f"**Script Type:** {p['script_type']}",
                    f"**Recommended Tools:** {', '.join(p['tools'])}",
                    f"**CI/CD Stage:** {p['cicd_step']}",
                    f"**Example Command:** `{p['example']}`\n",
                ]
                script = generate_script(pname, prereq, p)
                lines += [
                    "**Suggested Script:**",
                    f"```{p['automation']}",
                    script,
                    "```\n",
                ]
                automation_plan.append({
                    "name": name,
                    "pattern": pname,
                    "stage": p["cicd_step"],
                    "command": p["example"],
                    "automation": p["automation"],
                })
            else:
                safe_name = name.lower().replace(" ", "_")
                lines += [
                    "**⚠️ Automation:** Needs custom script\n",
                    "```python",
                    f"# scripts/{safe_name}_setup.py",
                    f"# TODO: Implement automation for: {desc}",
                    "import subprocess",
                    "def setup():",
                    f"    # Manual steps: {manual[:100]}...",
                    "    pass",
                    "if __name__ == '__main__': setup()",
                    "```\n",
                ]
                automation_plan.append({
                    "name": name,
                    "pattern": "custom",
                    "stage": "pre-test",
                    "command": f"python scripts/{safe_name}_setup.py",
                    "automation": "python",
                })

            lines.append("---\n")

        # CI/CD pipeline YAML (delegates to cicd_generators module)
        lines.append("# CI/CD Pipeline Integration\n")
        lines.append(generate_cicd_pipeline(automation_plan, fw, fw_cicd))

        # Framework-specific hooks
        if fw:
            lines.append("\n# Framework Integration\n")
            lines.append(generate_framework_hooks(automation_plan, fw))

        return "\n".join(lines)
