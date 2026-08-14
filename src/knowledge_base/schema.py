"""Pydantic models for framework knowledge base entries."""

from pydantic import BaseModel
from typing import Any


class CapabilityChange(BaseModel):
    """A single capability that was added, changed, or deprecated in a version."""

    name: str
    status: str  # "added" | "improved" | "deprecated" | "removed"
    description: str = ""
    replacement: str = ""  # what to use instead (for deprecated/removed)


class BreakingChange(BaseModel):
    """A breaking change introduced in a specific version."""

    description: str
    migration_effort: str = "low"  # "low" | "medium" | "high"
    affected_apis: list[str] = []
    workaround: str = ""


class FrameworkVersion(BaseModel):
    """Version-specific data for a framework release."""

    version: str  # e.g. "1.40", "4.0"
    release_date: str = ""  # ISO date string
    eol_date: str = ""  # end-of-life date (empty = still supported)
    is_lts: bool = False
    min_runtime: str = ""  # e.g. "Node 18+", "Python 3.8+"
    capabilities_added: list[CapabilityChange] = []
    capabilities_deprecated: list[CapabilityChange] = []
    breaking_changes: list[BreakingChange] = []
    notable_features: list[str] = []
    known_limitations: list[str] = []


class FrameworkData(BaseModel):
    """Schema for a single framework entry in the knowledge base."""

    framework_name: str
    vendor: str
    license: str
    website: str = ""
    first_release: str = ""
    latest_version: str = ""
    category: str = "test_automation"  # test_automation | cloud_infrastructure
    languages_supported: list[str]
    architecture_fit: dict[str, Any]
    capabilities: dict[str, Any]
    cicd_integration: dict[str, Any]
    cloud_grids: dict[str, Any] = {}
    cloud_providers: dict[str, Any] = {}  # Cloud provider support matrix
    testing_capabilities: dict[str, Any] = {}  # Testing features for IaC
    performance: dict[str, Any] = {}
    maintainability: dict[str, Any] = {}
    cloud_migration_metrics: dict[str, Any] = {}  # Cloud migration KPIs
    limitations: list[str] = []
    versions: list[FrameworkVersion] = []  # Version-specific history
