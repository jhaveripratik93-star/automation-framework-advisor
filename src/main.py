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
