"""Main FastAPI application - Automation Framework Migration Advisor."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.models import UserProfile
from src.knowledge_base import KnowledgeBase
from src.scoring import ScoringEngine, WeightProfile, DecisionMatrix
from src.migration.planner import MigrationPlanner
from src.migration.coverage import CoverageAnalyzer
from src.generator import BoilerplateGenerator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global instances
kb = KnowledgeBase(data_dir="data/frameworks")
scoring_engine: ScoringEngine | None = None
migration_planner = MigrationPlanner()
coverage_analyzer = CoverageAnalyzer()
boilerplate_generator = BoilerplateGenerator()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    global scoring_engine
    kb.load()
    scoring_engine = ScoringEngine(knowledge_base=kb)
    logger.info("Advisor Agent initialized successfully")

    # Auto-discover new frameworks on startup (non-blocking)
    try:
        from src.discovery.framework_scanner import FrameworkScanner
        scanner = FrameworkScanner(kb=kb, data_dir="data/frameworks")
        new_fws = scanner.scan(force=False)
        if new_fws:
            logger.info(
                "Framework scanner found %d new framework(s): %s",
                len(new_fws),
                [fw.name for fw in new_fws],
            )
    except Exception as exc:
        logger.debug("Auto-scan skipped on startup: %s", exc)

    yield
    logger.info("Advisor Agent shutting down")


app = FastAPI(
    title="Automation Framework Migration & Coverage Advisor",
    description="AI-powered advisor for selecting and migrating automation frameworks",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "service": "Automation Framework Migration Advisor",
        "status": "running",
        "frameworks_loaded": len(kb.list_all()),
    }


@app.get("/api/v1/frameworks")
async def list_frameworks():
    """List all frameworks in the knowledge base."""
    return {
        "frameworks": [
            {
                "name": fw.framework_name,
                "vendor": fw.vendor,
                "license": fw.license,
                "languages": fw.languages_supported,
            }
            for fw in kb.list_all()
        ]
    }


@app.get("/api/v1/frameworks/categorized")
async def list_frameworks_categorized():
    """List all frameworks in the knowledge base grouped by category."""
    from src.knowledge_base.schema import (
        FRAMEWORK_CATEGORIES, classify_framework_data,
    )

    categorized: dict[str, list] = {cat_id: [] for cat_id in FRAMEWORK_CATEGORIES}

    for fw in kb.list_all():
        cats = classify_framework_data(fw)
        fw_info = {
            "name": fw.framework_name,
            "vendor": fw.vendor,
            "license": fw.license,
            "languages": fw.languages_supported,
        }
        for cat in cats:
            if cat in categorized:
                categorized[cat].append(fw_info)

    return {
        cat_id: {
            "label": FRAMEWORK_CATEGORIES[cat_id]["label"],
            "icon": FRAMEWORK_CATEGORIES[cat_id]["icon"],
            "description": FRAMEWORK_CATEGORIES[cat_id]["description"],
            "frameworks": fws,
            "count": len(fws),
        }
        for cat_id, fws in categorized.items()
        if fws
    }


@app.post("/api/v1/evaluate", response_model=DecisionMatrix)
async def evaluate_frameworks(profile: UserProfile):
    """Evaluate and rank frameworks against a user profile."""
    if not scoring_engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    return scoring_engine.evaluate(profile)


@app.post("/api/v1/migration-plan")
async def generate_migration_plan(
    profile: UserProfile, target_framework: str = "Playwright"
):
    """Generate a migration roadmap for a selected framework."""
    roadmap = migration_planner.generate_roadmap(profile, target_framework)
    return roadmap.to_dict()


@app.post("/api/v1/coverage-analysis")
async def analyze_coverage(
    profile: UserProfile, target_framework: str = "Playwright"
):
    """Analyze coverage gaps for a target framework."""
    fw_data = kb.get(target_framework.lower())
    if not fw_data:
        raise HTTPException(
            status_code=404,
            detail=f"Framework '{target_framework}' not found in knowledge base",
        )
    report = coverage_analyzer.analyze(profile, fw_data)
    return report.to_dict()


@app.post("/api/v1/generate-boilerplate")
async def generate_boilerplate(
    profile: UserProfile, target_framework: str = "Playwright"
):
    """Generate a project boilerplate for the selected framework."""
    template = boilerplate_generator.generate(target_framework, profile)
    return {
        "framework": template.framework,
        "project_name": template.project_name,
        "file_tree": template.file_tree(),
        "files": [
            {"path": f.path, "content": f.content} for f in template.files
        ],
    }


@app.get("/api/v1/weight-presets")
async def get_weight_presets():
    """Return available weight preset profiles."""
    return WeightProfile.list_presets()


@app.post("/api/v1/discover-frameworks")
async def discover_frameworks(force: bool = False):
    """Scan the internet for new automation frameworks.

    Args:
        force: If True, ignore scan interval and scan immediately.

    Returns:
        Discovered frameworks grouped by category, plus a flat list for backward compat.
    """
    from src.discovery.framework_scanner import FrameworkScanner
    from src.knowledge_base.schema import FRAMEWORK_CATEGORIES

    scanner = FrameworkScanner(kb=kb, data_dir="data/frameworks")
    new_fws = scanner.scan(force=force)

    # Flat list (backward compatible)
    flat_list = [
        {
            "name": fw.name,
            "description": fw.description,
            "stars": fw.stars,
            "language": fw.language,
            "repo_url": fw.repo_url,
            "license": fw.license,
            "categories": fw.categories,
        }
        for fw in new_fws
    ]

    # Categorized view
    categorized = scanner.get_categorized_results(new_fws)
    categories_response = {
        cat_id: {
            "label": FRAMEWORK_CATEGORIES[cat_id]["label"],
            "icon": FRAMEWORK_CATEGORIES[cat_id]["icon"],
            "description": FRAMEWORK_CATEGORIES[cat_id]["description"],
            "frameworks": [
                {
                    "name": fw.name,
                    "description": fw.description,
                    "stars": fw.stars,
                    "language": fw.language,
                    "repo_url": fw.repo_url,
                    "license": fw.license,
                }
                for fw in fws
            ],
        }
        for cat_id, fws in categorized.items()
    }

    return {
        "new_frameworks": flat_list,
        "count": len(new_fws),
        "categorized": categories_response,
    }


@app.post("/api/v1/add-framework")
async def add_framework(name: str):
    """Research a framework and add it to the knowledge base.

    Args:
        name: Framework name to research and add.

    Returns:
        Status and profile path if successful.
    """
    from src.discovery.framework_scanner import FrameworkScanner
    from src.llm.groq_client import GroqClient
    from src.knowledge_base.schema import FrameworkData

    client = GroqClient()
    scanner = FrameworkScanner(
        kb=kb,
        llm_client=client if client.is_available else None,
        data_dir="data/frameworks",
    )
    profile = scanner.research_framework(name)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Could not research framework: {name}")

    # Validate completeness before adding
    is_complete, issues = FrameworkData.validate_for_addition(profile)

    path = scanner.add_framework(profile)
    if not path:
        raise HTTPException(status_code=500, detail=f"Could not write profile for: {name}")

    return {
        "status": "added",
        "framework": name,
        "path": str(path),
        "profile": profile,
        "completeness": {
            "is_complete": is_complete,
            "issues": issues,
        },
    }