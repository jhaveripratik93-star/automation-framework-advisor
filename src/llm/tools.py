"""Tool definitions and executor for function-calling LLM agents.

Provides a set of tools that the LLM can call to dynamically retrieve
context, run analyses, and interact with the knowledge base.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.graph import KnowledgeGraph, EntityType
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase
    from src.scoring import ScoringEngine
    from src.models import UserProfile

logger = logging.getLogger(__name__)


# ─── Tool Definitions (OpenAI Function Schema) ────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_graph",
            "description": "Search the knowledge graph for framework relationships, capabilities, and migration paths. Use this to find connections between frameworks, languages, and tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query about frameworks, e.g. 'Python frameworks for web testing' or 'migration from Selenium to Playwright'"
                    },
                    "max_hops": {
                        "type": "integer",
                        "description": "Maximum relationship hops to traverse (1-3). Default 2.",
                        "default": 2
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_framework_details",
            "description": "Get detailed information about a specific framework including capabilities, languages, CI/CD support, and limitations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "framework_name": {
                        "type": "string",
                        "description": "Name of the framework, e.g. 'Playwright', 'Selenium', 'Terraform'"
                    }
                },
                "required": ["framework_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_uploaded_content",
            "description": "Search through uploaded test files or case study documents for specific patterns or information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "What to search for in uploaded documents, e.g. 'migration timeline', 'testing patterns', 'framework version'"
                    },
                    "document_type": {
                        "type": "string",
                        "enum": ["test_files", "case_study", "all"],
                        "description": "Which uploaded documents to search",
                        "default": "all"
                    }
                },
                "required": ["search_term"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_framework_comparison",
            "description": "Compare two or more frameworks across all criteria and return detailed scoring breakdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "frameworks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of framework names to compare, e.g. ['Playwright', 'Cypress']"
                    },
                    "focus_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: specific criteria to focus on, e.g. ['language_compatibility', 'ci_cd_integration']"
                    }
                },
                "required": ["frameworks"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_migration_paths",
            "description": "Find possible migration paths from a current framework to target frameworks, including effort estimates and compatibility.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_framework": {
                        "type": "string",
                        "description": "Current framework name"
                    },
                    "to_frameworks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: target frameworks to evaluate. If omitted, finds all viable paths."
                    }
                },
                "required": ["from_framework"]
            }
        }
    }
]


# ─── Tool Executor ─────────────────────────────────────────────────────

class ToolExecutor:
    """Executes tool calls requested by the LLM."""

    def __init__(
        self,
        knowledge_graph: KnowledgeGraph,
        graphrag_engine: GraphRAGEngine,
        knowledge_base: KnowledgeBase,
        uploaded_docs_context: str = "",
        case_study_context: str = "",
    ):
        self.kg = knowledge_graph
        self.graphrag = graphrag_engine
        self.kb = knowledge_base
        self.uploaded_docs = uploaded_docs_context
        self.case_study = case_study_context
        
        # Map tool names to handler methods
        self.tools: dict[str, Callable] = {
            "search_knowledge_graph": self._search_knowledge_graph,
            "get_framework_details": self._get_framework_details,
            "analyze_uploaded_content": self._analyze_uploaded_content,
            "run_framework_comparison": self._run_framework_comparison,
            "find_migration_paths": self._find_migration_paths,
        }

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call and return the result as a string."""
        logger.info("=" * 80)
        logger.info(f"TOOL_EXECUTOR.execute: Tool='{tool_name}'")
        logger.info(f"  Arguments: {arguments}")
        logger.info("=" * 80)
        
        if tool_name not in self.tools:
            logger.error(f"TOOL_EXECUTOR: Unknown tool '{tool_name}' (available: {list(self.tools.keys())})")
            return f"Error: Unknown tool '{tool_name}'"
        
        try:
            logger.info(f"TOOL_EXECUTOR: Calling handler for '{tool_name}'...")
            result = self.tools[tool_name](**arguments)
            logger.info(f"TOOL_EXECUTOR: SUCCESS - '{tool_name}' returned {len(str(result))} chars")
            logger.debug(f"  Result preview: {str(result)[:200]}...")
            return result
        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"TOOL_EXECUTOR: FAILED - '{tool_name}' raised exception")
            logger.error(f"  Error: {type(e).__name__}: {e}")
            logger.error("=" * 80)
            return f"Error executing {tool_name}: {str(e)}"

    # ─── Tool Implementations ──────────────────────────────────────────

    def _search_knowledge_graph(self, query: str, max_hops: int = 2) -> str:
        """Search the knowledge graph for relevant relationships."""
        logger.info(f"TOOL: search_knowledge_graph (query='{query[:50]}...', max_hops={max_hops})")
        # Use GraphRAG engine to retrieve context
        context = self.graphrag.retrieve_context(query)
        
        if not context or context == "No relevant knowledge graph data found.":
            logger.info(f"TOOL: search_knowledge_graph - No results found")
            return f"No knowledge graph results found for: {query}"
        
        logger.info(f"TOOL: search_knowledge_graph - Found {len(context)} chars of context")
        return f"Knowledge Graph Results for '{query}':\n{context}"

    def _get_framework_details(self, framework_name: str) -> str:
        """Get detailed framework information from the knowledge base."""
        logger.info(f"TOOL: get_framework_details (framework='{framework_name}')")
        fw_data = self.kb.get(framework_name.lower())
        
        if not fw_data:
            logger.info(f"TOOL: get_framework_details - Framework '{framework_name}' not found")
            return f"Framework '{framework_name}' not found in knowledge base."
        
        logger.info(f"TOOL: get_framework_details - Retrieved details for '{fw_data.framework_name}'")
        
        details = [
            f"## {fw_data.framework_name}",
            f"**Languages:** {', '.join(fw_data.languages_supported)}",
            f"**License:** {fw_data.license}",
            f"**Architecture Fit:** {', '.join(k for k, v in fw_data.architecture_fit.items() if v is True)}",
            "",
            "**Key Capabilities:**"
        ]
        
        for k, v in fw_data.capabilities.items():
            if v is True or (isinstance(v, str) and v not in ("", "false")):
                details.append(f"  - {k.replace('_', ' ').title()}: {v}")
        
        if fw_data.limitations:
            details.append("\n**Limitations:**")
            for lim in fw_data.limitations[:5]:
                details.append(f"  - {lim}")
        
        return "\n".join(details)

    def _analyze_uploaded_content(self, search_term: str, document_type: str = "all") -> str:
        """Search uploaded documents for specific content."""
        logger.info(f"TOOL: analyze_uploaded_content (search='{search_term}', doc_type='{document_type}')")
        
        results = []
        search_lower = search_term.lower()
        
        if document_type in ("test_files", "all") and self.uploaded_docs:
            # Simple keyword search in uploaded test files
            lines = self.uploaded_docs.split("\n")
            matches = [line for line in lines if search_lower in line.lower()]
            if matches:
                logger.info(f"TOOL: analyze_uploaded_content - Found {len(matches)} matches in test files")
                results.append(f"**Test Files ({len(matches)} matches):**")
                results.extend(matches[:10])  # Limit to 10 matches
        
        if document_type in ("case_study", "all") and self.case_study:
            # Simple keyword search in case study
            lines = self.case_study.split("\n")
            matches = [line for line in lines if search_lower in line.lower()]
            if matches:
                logger.info(f"TOOL: analyze_uploaded_content - Found {len(matches)} matches in case study")
                results.append(f"\n**Case Study ({len(matches)} matches):**")
                results.extend(matches[:10])
        
        if not results:
            logger.info(f"TOOL: analyze_uploaded_content - No matches found")
            return f"No matches found for '{search_term}' in uploaded documents."
        
        return "\n".join(results)

    def _run_framework_comparison(self, frameworks: list[str], focus_criteria: list[str] | None = None) -> str:
        """Compare frameworks using the scoring engine."""
        logger.info(f"TOOL: run_framework_comparison (frameworks={frameworks}, criteria={focus_criteria})")
        
        comparison = [f"## Framework Comparison: {' vs '.join(frameworks)}\n"]
        
        for fw_name in frameworks:
            fw_data = self.kb.get(fw_name.lower())
            if not fw_data:
                logger.warning(f"TOOL: run_framework_comparison - Framework '{fw_name}' not found")
                comparison.append(f"- **{fw_name}**: Not found in knowledge base")
                continue
            
            logger.info(f"TOOL: run_framework_comparison - Processing '{fw_data.framework_name}'")
            
            # Get basic info
            comparison.append(f"### {fw_data.framework_name}")
            comparison.append(f"- **Languages:** {', '.join(fw_data.languages_supported)}")
            comparison.append(f"- **License:** {fw_data.license}")
            
            # Highlight key capabilities
            caps = [k.replace('_', ' ').title() for k, v in fw_data.capabilities.items() 
                   if v is True or (isinstance(v, str) and v not in ("", "false"))]
            comparison.append(f"- **Capabilities:** {', '.join(caps[:5])}")
            comparison.append("")
        
        logger.info(f"TOOL: run_framework_comparison - Completed comparison of {len(frameworks)} frameworks")
        return "\n".join(comparison)

    def _find_migration_paths(self, from_framework: str, to_frameworks: list[str] | None = None) -> str:
        """Find migration paths between frameworks."""
        logger.info(f"TOOL: find_migration_paths (from='{from_framework}', to={to_frameworks})")
        
        # Search for MIGRATES_TO relationships in the knowledge graph
        from src.graph import RelationshipType
        
        source_entities = self.kg.find_entities_by_name(from_framework)
        if not source_entities:
            logger.info(f"TOOL: find_migration_paths - Source framework '{from_framework}' not found in graph")
            return f"Framework '{from_framework}' not found in knowledge graph."
        
        source_id = source_entities[0].id
        relationships = self.kg.get_relationships(source_id, hops=2)
        
        # Filter for MIGRATES_TO relationships
        migration_paths = [
            rel for rel in relationships 
            if rel.relationship_type == RelationshipType.MIGRATES_TO
        ]
        
        if not migration_paths:
            logger.info(f"TOOL: find_migration_paths - No migration paths found from '{from_framework}'")
            return f"No migration paths found from '{from_framework}'."
        
        logger.info(f"TOOL: find_migration_paths - Found {len(migration_paths)} migration path(s)")
        
        results = [f"## Migration Paths from {from_framework}\n"]
        for rel in migration_paths:
            target = self.kg.get_entity(rel.target_id)
            if target:
                conf_pct = int(rel.confidence * 100)
                results.append(f"- **{target.name}** (confidence: {conf_pct}%)")
                if rel.properties:
                    for k, v in rel.properties.items():
                        results.append(f"  - {k}: {v}")
        
        return "\n".join(results)
