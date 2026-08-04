"""Pydantic models for framework knowledge base entries."""

from pydantic import BaseModel
from typing import Any


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
