"""Chat-based advisor that answers questions about framework recommendations.

Uses rule-based intent matching for the POC. Can be upgraded to LLM-based
responses in Phase 2 by swapping the `respond()` method.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.models import UserProfile
from src.knowledge_base import KnowledgeBase, FrameworkData
from src.scoring.models import DecisionMatrix, FrameworkScore


@dataclass
class ChatMessage:
    role: str  # "user" or "assistant"
    content: str


class AdvisorChat:
    """Context-aware conversational advisor."""

    def __init__(self, knowledge_base: KnowledgeBase):
        self.kb = knowledge_base
        self.history: list[ChatMessage] = []
        self.profile: UserProfile | None = None
        self.matrix: DecisionMatrix | None = None

    def set_context(
        self, profile: UserProfile | None, matrix: DecisionMatrix | None
    ):
        """Update the chat context with latest evaluation results."""
        self.profile = profile
        self.matrix = matrix

    def respond(self, user_message: str) -> str:
        """Generate a response to the user's message."""
        self.history.append(ChatMessage(role="user", content=user_message))
        msg = user_message.lower().strip()

        # Route to appropriate handler
        if any(w in msg for w in ["why", "reason", "explain", "how come"]):
            response = self._handle_why(msg)
        elif any(w in msg for w in ["compare", "vs", "versus", "difference"]):
            response = self._handle_compare(msg)
        elif any(w in msg for w in ["what if", "change", "switch", "instead"]):
            response = self._handle_what_if(msg)
        elif any(w in msg for w in ["limitation", "drawback", "con", "weakness", "problem"]):
            response = self._handle_limitations(msg)
        elif any(w in msg for w in ["recommend", "suggest", "best", "which", "should i"]):
            response = self._handle_recommend(msg)
        elif any(w in msg for w in ["tell me about", "what is", "describe", "info"]):
            response = self._handle_info(msg)
        elif any(w in msg for w in ["migrate", "migration", "roadmap", "plan", "steps"]):
            response = self._handle_migration(msg)
        elif any(w in msg for w in ["cost", "license", "free", "open source", "pricing"]):
            response = self._handle_cost(msg)
        elif any(w in msg for w in ["hello", "hi", "hey", "help"]):
            response = self._handle_greeting()
        else:
            response = self._handle_general(msg)

        self.history.append(ChatMessage(role="assistant", content=response))
        return response

    def _handle_greeting(self) -> str:
        if self.matrix:
            top = self.matrix.rankings[0] if self.matrix.rankings else None
            return (
                "Hey! I'm your Framework Advisor. I've already evaluated your project and "
                f"**{top.framework}** ({top.overall_score}/100) is the top recommendation.\n\n"
                "You can ask me things like:\n"
                "- *Why did Playwright score higher than Cypress?*\n"
                "- *What if my team uses TypeScript instead?*\n"
                "- *Compare the top 3 options*\n"
                "- *What are Terraform's limitations?*\n"
                "- *How long will migration take?*"
            )
        return (
            "Hey! I'm your Framework Advisor. Fill in the discovery form first, "
            "then come back here to discuss the results. I can help you:\n\n"
            "- Understand why a framework was recommended\n"
            "- Compare frameworks head-to-head\n"
            "- Explore what-if scenarios\n"
            "- Dig into limitations and trade-offs"
        )

    def _handle_why(self, msg: str) -> str:
        """Explain why a framework scored the way it did."""
        if not self.matrix or not self.matrix.rankings:
            return "Run the evaluation first, then I can explain the scoring."

        # Find which framework they're asking about
        fw_name = self._extract_framework_name(msg)
        if fw_name:
            score = self._find_score(fw_name)
            if score:
                return self._explain_score(score)

        # Default: explain top pick
        top = self.matrix.rankings[0]
        runner_up = self.matrix.rankings[1] if len(self.matrix.rankings) > 1 else None

        explanation = f"**{top.framework}** scored {top.overall_score}/100 because:\n\n"
        scores = top.criteria_scores.model_dump()
        labels = {
            "C1_language_compatibility": "Language fit",
            "C2_api_validation": "API support",
            "C3_performance_load": "Performance",
            "C4_cicd_integration": "CI/CD",
            "C5_maintainability": "Maintainability",
            "C6_cloud_readiness": "Cloud readiness",
            "C7_license_cost": "Licensing",
        }
        # Top strengths
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        explanation += "**Strengths:**\n"
        for c_id, val in sorted_scores[:3]:
            explanation += f"- {labels.get(c_id, c_id)}: {val}/100\n"

        if runner_up:
            diff = top.overall_score - runner_up.overall_score
            explanation += (
                f"\nIt beat **{runner_up.framework}** by {diff:.1f} points, "
                f"mainly due to better scores in the areas above."
            )
        return explanation

    def _handle_compare(self, msg: str) -> str:
        """Compare two or more frameworks."""
        if not self.matrix:
            return "Run the evaluation first so I have scores to compare."

        frameworks = self._extract_multiple_frameworks(msg)
        if len(frameworks) < 2:
            # Compare top 3 by default
            frameworks = [r.framework for r in self.matrix.rankings[:3]]

        labels = {
            "C1_language_compatibility": "Language",
            "C2_api_validation": "API",
            "C3_performance_load": "Perf",
            "C4_cicd_integration": "CI/CD",
            "C5_maintainability": "Maint.",
            "C6_cloud_readiness": "Cloud",
            "C7_license_cost": "License",
        }

        response = "**Head-to-head comparison:**\n\n"
        response += "| Criterion | " + " | ".join(frameworks) + " |\n"
        response += "|---|" + "|".join(["---"] * len(frameworks)) + "|\n"

        # Get scores for each
        fw_scores = []
        for fw_name in frameworks:
            score = self._find_score(fw_name)
            fw_scores.append(score)

        for c_id, label in labels.items():
            row = f"| {label} |"
            for score in fw_scores:
                if score:
                    val = getattr(score.criteria_scores, c_id, 0)
                    row += f" {val} |"
                else:
                    row += " - |"
            response += row + "\n"

        response += "| **Overall** |"
        for score in fw_scores:
            if score:
                response += f" **{score.overall_score}** |"
            else:
                response += " - |"
        response += "\n"

        # Add a verdict
        best = max(
            (s for s in fw_scores if s),
            key=lambda s: s.overall_score,
            default=None,
        )
        if best:
            response += f"\n**Verdict:** {best.framework} is the stronger choice for your profile."

        return response

    def _handle_what_if(self, msg: str) -> str:
        """Handle hypothetical scenarios."""
        if not self.profile:
            return "Submit the discovery form first, then I can explore what-if scenarios."

        # Detect language change
        langs = ["python", "typescript", "javascript", "java", "c#", "go", "ruby"]
        for lang in langs:
            if lang in msg:
                return self._what_if_language(lang)

        # Detect tool changes
        if any(w in msg for w in ["docker", "container"]):
            return self._what_if_docker()
        if any(w in msg for w in ["budget", "commercial", "paid"]):
            return self._what_if_budget()
        if any(w in msg for w in ["cloud", "terraform", "ansible"]):
            return self._what_if_cloud()

        return (
            "I can simulate changes like:\n"
            "- *What if we switch to TypeScript?*\n"
            "- *What if we add Docker support?*\n"
            "- *What if budget is flexible?*\n"
            "- *What if we include cloud migration?*\n\n"
            "Try asking one of these!"
        )

    def _what_if_language(self, lang: str) -> str:
        lang_title = lang.title()
        # Frameworks that support this language
        supporting = []
        for fw in self.kb.list_all():
            fw_langs = [l.lower() for l in fw.languages_supported]
            if lang in fw_langs or any(lang in l for l in fw_langs):
                supporting.append(fw.framework_name)

        response = f"**What if your team uses {lang_title}?**\n\n"
        response += f"Frameworks with native {lang_title} support: "
        response += ", ".join(supporting[:8]) + "\n\n"

        if self.matrix and self.matrix.rankings:
            top = self.matrix.rankings[0]
            fw_data = self.kb.get(top.framework.lower())
            if fw_data:
                fw_langs = [l.lower() for l in fw_data.languages_supported]
                if lang in fw_langs or any(lang in l for l in fw_langs):
                    response += f"Good news — **{top.framework}** already supports {lang_title}. "
                    response += "The recommendation wouldn't change much."
                else:
                    response += (
                        f"**{top.framework}** does NOT support {lang_title}. "
                        f"With that change, **{supporting[0] if supporting else 'unknown'}** "
                        f"would likely become the top recommendation."
                    )
        return response

    def _what_if_docker(self) -> str:
        return (
            "**What if you add Docker containerization?**\n\n"
            "Adding Docker as a requirement boosts CI/CD scores for frameworks "
            "with pre-built Docker images (Playwright, Selenium, K6). "
            "It would penalize frameworks without Docker support.\n\n"
            "Toggle 'Docker containers' in the Environment tab and re-evaluate to see the impact."
        )

    def _what_if_budget(self) -> str:
        return (
            "**What if budget is flexible?**\n\n"
            "Opening the budget allows commercial options like:\n"
            "- **Cypress Cloud** for parallel execution\n"
            "- **Terraform Cloud/Enterprise** for team collaboration\n"
            "- **Sauce Labs / BrowserStack** for cloud grids\n\n"
            "The license cost criterion (C7) weight would decrease, potentially "
            "lifting frameworks with paid cloud features higher."
        )

    def _what_if_cloud(self) -> str:
        return (
            "**What if you include cloud infrastructure migration?**\n\n"
            "Enabling cloud migration adds 5 frameworks to the evaluation:\n"
            "- **Terraform** — Multi-cloud IaC, strongest ecosystem\n"
            "- **Pulumi** — IaC in real languages (Python/TS/Go)\n"
            "- **Ansible** — Configuration management + provisioning\n"
            "- **Chef** — Compliance-heavy, InSpec testing\n"
            "- **AWS CloudFormation** — AWS-native, zero setup\n\n"
            "Toggle 'Cloud infrastructure evaluation' in the Cloud tab to include them."
        )

    def _handle_limitations(self, msg: str) -> str:
        """List limitations of a specific framework."""
        fw_name = self._extract_framework_name(msg)
        if not fw_name:
            if self.matrix and self.matrix.rankings:
                fw_name = self.matrix.rankings[0].framework
            else:
                return "Which framework would you like to know the limitations of?"

        fw_data = self.kb.get(fw_name.lower())
        if not fw_data:
            # Try partial match
            for fw in self.kb.list_all():
                if fw_name.lower() in fw.framework_name.lower():
                    fw_data = fw
                    break
        if not fw_data:
            return f"I don't have data on '{fw_name}'. Try: {', '.join(self.kb.list_names()[:5])}"

        response = f"**{fw_data.framework_name} — Known Limitations:**\n\n"
        for lim in fw_data.limitations:
            response += f"- {lim}\n"
        return response

    def _handle_recommend(self, msg: str) -> str:
        """Give a recommendation with reasoning."""
        if not self.matrix or not self.matrix.rankings:
            return "Run the evaluation first and I'll give you a personalized recommendation."

        top = self.matrix.rankings[0]
        response = (
            f"Based on your profile, I recommend **{top.framework}** "
            f"(score: {top.overall_score}/100, confidence: {top.confidence}).\n\n"
        )
        if top.pros:
            response += "**Key reasons:**\n"
            for pro in top.pros[:4]:
                response += f"- {pro}\n"
        if top.cons:
            response += "\n**Watch out for:**\n"
            for con in top.cons[:3]:
                response += f"- {con}\n"

        if len(self.matrix.rankings) > 1:
            runner = self.matrix.rankings[1]
            response += (
                f"\n**Runner-up:** {runner.framework} ({runner.overall_score}/100) — "
                f"a solid alternative if {top.cons[0] if top.cons else 'any limitation'} "
                f"is a dealbreaker."
            )
        return response

    def _handle_info(self, msg: str) -> str:
        """Provide info about a framework."""
        fw_name = self._extract_framework_name(msg)
        if not fw_name:
            return "Which framework would you like to know about? I have info on: " + \
                   ", ".join(self.kb.list_names()[:8]) + "..."

        fw_data = self.kb.get(fw_name.lower())
        if not fw_data:
            for fw in self.kb.list_all():
                if fw_name.lower() in fw.framework_name.lower():
                    fw_data = fw
                    break
        if not fw_data:
            return f"I don't have '{fw_name}' in my knowledge base."

        response = f"**{fw_data.framework_name}**\n\n"
        response += f"- **Vendor:** {fw_data.vendor}\n"
        response += f"- **License:** {fw_data.license}\n"
        response += f"- **Languages:** {', '.join(fw_data.languages_supported)}\n"
        response += f"- **Category:** {getattr(fw_data, 'category', 'test automation')}\n"

        # Key capabilities
        caps = fw_data.capabilities or {}
        highlights = []
        if caps.get("parallel_execution"):
            highlights.append(f"Parallel: {caps['parallel_execution']}")
        if caps.get("auto_wait") is True:
            highlights.append("Auto-wait")
        if caps.get("network_interception") is True:
            highlights.append("Network interception")
        if highlights:
            response += f"- **Highlights:** {', '.join(highlights)}\n"

        return response

    def _handle_migration(self, msg: str) -> str:
        """Answer migration-related questions."""
        if not self.profile:
            return "Submit the discovery form first so I can estimate migration effort."

        total = self.profile.legacy_test_count
        if total == 0:
            return "You haven't specified any legacy test scripts. Set a count in the Requirements tab."

        team_cap = max(1, self.profile.team_size // 2)
        weeks = max(4, total // (team_cap * 8))
        weeks = min(weeks, self.profile.timeline_weeks)

        return (
            f"**Migration Estimate:**\n\n"
            f"- Scripts to migrate: **{total}**\n"
            f"- Team capacity (half team): **{team_cap}** engineers\n"
            f"- Estimated effort: **{weeks} weeks**\n"
            f"- Your timeline: {self.profile.timeline_weeks} weeks\n\n"
            f"{'✅ Feasible within timeline.' if weeks <= self.profile.timeline_weeks else '⚠️ May exceed timeline — consider phased migration or more team allocation.'}\n\n"
            f"Click **Next: Migration Plan** to see the full phased roadmap."
        )

    def _handle_cost(self, msg: str) -> str:
        """Answer cost/licensing questions."""
        if not self.matrix:
            return "Run the evaluation first for cost analysis."

        response = "**License & Cost Overview:**\n\n"
        for r in self.matrix.rankings[:5]:
            fw = self.kb.get(r.framework.lower())
            if fw:
                license_score = r.criteria_scores.C7_license_cost
                icon = "🟢" if license_score >= 80 else "🟡" if license_score >= 50 else "🔴"
                response += f"{icon} **{fw.framework_name}** — {fw.license} (score: {license_score}/100)\n"
        return response

    def _handle_general(self, msg: str) -> str:
        """Fallback handler."""
        fw_name = self._extract_framework_name(msg)
        if fw_name:
            return self._handle_info(msg)

        return (
            "I can help you with:\n\n"
            "- **Why** a framework was recommended\n"
            "- **Compare** two or more frameworks\n"
            "- **What if** scenarios (language, budget, cloud)\n"
            "- **Limitations** of a specific tool\n"
            "- **Migration** effort estimates\n"
            "- **Cost** and licensing details\n\n"
            "Try asking a question like: *Why is Terraform ranked first?*"
        )

    def _explain_score(self, score: FrameworkScore) -> str:
        """Detailed explanation for a specific framework's score."""
        scores = score.criteria_scores.model_dump()
        labels = {
            "C1_language_compatibility": "Language fit",
            "C2_api_validation": "API support",
            "C3_performance_load": "Performance",
            "C4_cicd_integration": "CI/CD integration",
            "C5_maintainability": "Maintainability",
            "C6_cloud_readiness": "Cloud readiness",
            "C7_license_cost": "License/cost",
        }
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        response = f"**{score.framework}** scored **{score.overall_score}/100**:\n\n"
        for c_id, val in sorted_scores:
            bar = "█" * (val // 10) + "░" * (10 - val // 10)
            response += f"- {labels.get(c_id, c_id)}: `{bar}` {val}\n"

        if score.penalties_applied:
            response += "\n**Penalties applied:**\n"
            for p in score.penalties_applied:
                response += f"- ❌ {p}\n"
        if score.bonuses_applied:
            response += "\n**Bonuses earned:**\n"
            for b in score.bonuses_applied:
                response += f"- ✅ {b}\n"
        return response

    def _extract_framework_name(self, msg: str) -> str | None:
        """Try to extract a framework name from the message."""
        known = [fw.framework_name.lower() for fw in self.kb.list_all()]
        # Also check common short names
        aliases = {
            "playwright": "playwright",
            "cypress": "cypress",
            "selenium": "selenium webdriver",
            "webdriverio": "webdriverio",
            "wdio": "webdriverio",
            "robot": "robot framework",
            "robot framework": "robot framework",
            "k6": "k6",
            "locust": "locust",
            "appium": "appium",
            "testcafe": "testcafe",
            "puppeteer": "puppeteer",
            "karate": "karate",
            "rest assured": "rest assured",
            "terraform": "terraform",
            "ansible": "ansible",
            "chef": "chef",
            "pulumi": "pulumi",
            "cloudformation": "aws cloudformation",
            "cfn": "aws cloudformation",
        }
        for alias, full_name in aliases.items():
            if alias in msg:
                return full_name
        for name in known:
            if name in msg:
                return name
        return None

    def _extract_multiple_frameworks(self, msg: str) -> list[str]:
        """Extract multiple framework names from a message."""
        found = []
        aliases = {
            "playwright": "Playwright",
            "cypress": "Cypress",
            "selenium": "Selenium WebDriver",
            "webdriverio": "WebdriverIO",
            "robot": "Robot Framework",
            "k6": "K6",
            "terraform": "Terraform",
            "ansible": "Ansible",
            "chef": "Chef",
            "pulumi": "Pulumi",
            "cloudformation": "AWS CloudFormation",
        }
        for alias, display_name in aliases.items():
            if alias in msg:
                found.append(display_name)
        return found

    def _find_score(self, fw_name: str) -> FrameworkScore | None:
        """Find a framework's score in the current matrix."""
        if not self.matrix:
            return None
        for r in self.matrix.rankings:
            if fw_name.lower() in r.framework.lower():
                return r
        return None
