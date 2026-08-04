"""Shared data models for the Advisor Agent."""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class ArchitectureType(str, Enum):
    WEB_SPA = "web_spa"
    WEB_MPA = "web_mpa"
    NATIVE_MOBILE = "native_mobile"
    HYBRID_MOBILE = "hybrid_mobile"
    DESKTOP = "desktop"
    API_ONLY = "api_only"
    MICROSERVICES = "microservices"
    CLOUD_INFRASTRUCTURE = "cloud_infrastructure"


class CloudProvider(str, Enum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    MULTI_CLOUD = "multi_cloud"
    HYBRID = "hybrid_cloud"
    ON_PREMISE = "on_premise"


class ExperienceLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class BudgetPreference(str, Enum):
    STRICT_OSS = "strict_oss"
    OSS_PREFERRED = "oss_preferred"
    FLEXIBLE = "flexible"
    COMMERCIAL_OK = "commercial_ok"


class UserProfile(BaseModel):
    """Structured profile captured from Interactive Discovery."""

    project_name: str
    architecture_types: list[ArchitectureType]
    primary_language: str
    secondary_languages: list[str] = []
    team_size: int = Field(ge=1)
    automation_experience: ExperienceLevel
    current_framework: Optional[str] = None
    ci_cd_tool: str
    containerized: bool = False
    cloud_grid: Optional[str] = None
    parallel_required: bool = False
    browsers_required: list[str] = []
    special_ui: list[str] = []
    budget: BudgetPreference = BudgetPreference.OSS_PREFERRED
    timeline_weeks: int = Field(default=12, ge=1)
    must_support: list[str] = []
    nice_to_have: list[str] = []
    legacy_test_count: int = Field(default=0, ge=0)
    legacy_frameworks: list[str] = []

    # Cloud migration specific fields
    cloud_migration: bool = False
    cloud_providers: list[CloudProvider] = []
    cloud_services_used: list[str] = []  # e.g., EC2, Lambda, S3, RDS
    infrastructure_scale: str = ""  # small, medium, large, enterprise
    compliance_requirements: list[str] = []  # SOC2, HIPAA, PCI-DSS, etc.
    existing_iac_tool: Optional[str] = None  # current IaC tool if any
    multi_account: bool = False  # multi-account/multi-region deployment
    immutable_infrastructure: bool = False  # containers/serverless vs mutable VMs
