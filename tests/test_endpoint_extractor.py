"""Tests for src/analysis/endpoint_extractor.py — deriving a keyword -> HTTP
endpoint map from custom-library source so the Robot->Playwright converter can
translate keyword calls into native Playwright APIRequestContext calls instead
of importing the legacy client.

The REST-client tests run against the REAL clamps/nifi/nifi.py +
common/rest.py + configs/common_config.py fixtures on disk; they skip (early
return) when the fixture tree is absent, matching the degrade-don't-crash
contract of the other analyzer tests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analysis.endpoint_extractor import (
    build_endpoint_context,
    extract_endpoints_from_source,
)

_AUTOTEST_ROOT = (
    Path(__file__).parent.parent.parent / "autotest_2_extracted" / "autotest"
)
_NIFI_PY = _AUTOTEST_ROOT / "clamps" / "nifi" / "nifi.py"
_REST_PY = _AUTOTEST_ROOT / "common" / "rest.py"


# ──────────────────────────────────────────────────────────────────────
# extract_endpoints_from_source — synthetic + real nifi source
# ──────────────────────────────────────────────────────────────────────

def test_extract_from_synthetic_rest_client():
    src = '''
class Widget:
    def __init__(self, host, server_port=8080, base_path="/api", https_enabled=True):
        self.rest = Rest(host, server_port, base_path, https_enabled=https_enabled)

    def get_status(self):
        return self.rest.get("status")

    def create_thing(self, body):
        return self.rest.post("things", body=body)

    def _private(self):
        return self.rest.get("secret")
'''
    client = extract_endpoints_from_source(src, service_hint="widget")
    assert client.is_rest_client
    assert client.class_name == "Widget"
    assert client.default_port == "8080"
    assert client.default_base_path == "/api"
    assert client.https_enabled is True
    assert client.service_hint == "widget"

    eps = {e.method_name: (e.http_method, e.path) for e in client.endpoints}
    assert eps["get_status"] == ("GET", "status")
    assert eps["create_thing"] == ("POST", "things")
    # private helpers are not Robot keywords -> excluded
    assert "_private" not in eps


def test_extract_requests_module_client():
    src = '''
class ApiThing:
    def fetch(self):
        return requests.get("/health")
'''
    client = extract_endpoints_from_source(src)
    assert client.is_rest_client
    eps = {e.method_name: (e.http_method, e.path) for e in client.endpoints}
    assert eps["fetch"] == ("GET", "/health")


def test_non_rest_class_yields_empty():
    src = '''
class Driver:
    def do_stuff(self):
        self.session.execute("SELECT 1")  # not an HTTP verb
        return 42
'''
    client = extract_endpoints_from_source(src)
    assert not client.is_rest_client
    assert client.endpoints == []


def test_non_literal_path_is_skipped():
    """A path built from an f-string / variable is NOT safely reducible, so the
    method is omitted (left to the bundling fallback)."""
    src = '''
class Dyn:
    def by_id(self, entity_id):
        return self.rest.get(f"things/{entity_id}")
'''
    client = extract_endpoints_from_source(src)
    assert not client.is_rest_client


def test_syntax_error_source_returns_empty_not_raise():
    client = extract_endpoints_from_source("class Broken(:\n    def x(")
    assert not client.is_rest_client


def test_extract_from_real_nifi_source():
    if not _NIFI_PY.is_file():
        return
    text = _NIFI_PY.read_text(encoding="utf-8", errors="replace")
    client = extract_endpoints_from_source(text, service_hint="nifi")

    assert client.is_rest_client
    assert client.class_name == "Nifi"
    # __init__ defaults: server_port=7777, base_path="/nifi-api"
    assert client.default_port == "7777"
    assert client.default_base_path == "/nifi-api"

    eps = {e.method_name: (e.http_method, e.path) for e in client.endpoints}
    # Nifi.get_flow_status -> self.rest.get("flow/status") -> GET flow/status
    assert eps["get_flow_status"] == ("GET", "flow/status")
    # a couple more confirm the GET derivation generalizes
    assert eps["get_flow_about"] == ("GET", "flow/about")
    via = next(e.via for e in client.endpoints if e.method_name == "get_flow_status")
    assert via == "self.rest.get"


# ──────────────────────────────────────────────────────────────────────
# build_endpoint_context — consumes an analyzer-shaped analysis dict
# ──────────────────────────────────────────────────────────────────────

def test_build_context_from_analysis_shape():
    if not _NIFI_PY.is_file():
        return
    analysis = {
        "source_root": str(_AUTOTEST_ROOT),
        "files": [
            {
                "file_path": "nifi.robot",
                "settings": {
                    "libraries": [
                        {
                            "raw_module": "clamps.nifi.nifi.Nifi",
                            "alias": "Nifi",
                            "args": ["nifi"],
                            "resolved_path": str(_NIFI_PY),
                            "exists": True,
                            "kind": "custom",
                        }
                    ],
                },
                "test_cases": [],
            }
        ],
    }
    context = build_endpoint_context(analysis, str(_AUTOTEST_ROOT))
    assert context
    # endpoint map header + native-translation instruction
    assert "endpoint map" in context.lower()
    assert "APIRequestContext" in context
    # derived flow/status mapping + base-url pieces
    assert "get_flow_status" in context
    assert "GET /nifi-api/flow/status" in context
    assert "port=`7777`" in context
    assert "service/host=`nifi`" in context
    # resolved source included verbatim for header/auth reference (the file is
    # large, so only the capped head is embedded — assert on content known to
    # be near the top: the class + its __init__ defaults).
    assert "Resolved custom-library source" in context
    assert "class Nifi" in context
    assert 'base_path="/nifi-api"' in context


def test_build_context_empty_when_no_custom_libs():
    analysis = {"source_root": "", "files": [{"file_path": "x.robot", "settings": {"libraries": []}, "test_cases": []}]}
    assert build_endpoint_context(analysis) == ""


def test_build_context_empty_when_library_unresolved():
    analysis = {
        "source_root": "",
        "files": [
            {
                "file_path": "x.robot",
                "settings": {
                    "libraries": [
                        {
                            "raw_module": "clamps.nifi.nifi.Nifi",
                            "resolved_path": None,
                            "exists": False,
                            "kind": "unresolved",
                        }
                    ]
                },
                "test_cases": [],
            }
        ],
    }
    assert build_endpoint_context(analysis) == ""
