"""Tests for src/analysis/endpoint_extractor.py — deriving a keyword -> HTTP
endpoint map from custom-library source so the Robot->Playwright converter can
translate keyword calls into native Playwright APIRequestContext calls instead
of importing the legacy client.

The REST-client tests run against a SELF-CONTAINED synthetic custom-library
source built under pytest's ``tmp_path`` (see tests/conftest.py), so they run
everywhere without depending on any external fixture tree.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analysis.endpoint_extractor import (
    build_endpoint_context,
    extract_endpoints_from_source,
)


# ──────────────────────────────────────────────────────────────────────
# extract_endpoints_from_source — synthetic source
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


def test_extract_from_synthetic_client_source(make_robot_suite):
    """Against the synthetic Client library source on disk, the extractor reads
    the REAL literal paths (never guessed) and the __init__ base-url defaults."""
    suite = make_robot_suite()
    text = suite.client_py.read_text(encoding="utf-8", errors="replace")
    client = extract_endpoints_from_source(text, service_hint="widgets")

    assert client.is_rest_client
    assert client.class_name == "Client"
    # __init__ defaults: server_port=8080, base_path="/widgets-api"
    assert client.default_port == "8080"
    assert client.default_base_path == "/widgets-api"

    eps = {e.method_name: (e.http_method, e.path) for e in client.endpoints}
    # Client.get_thing_status -> self.rest.get("things/status") -> GET things/status
    assert eps["get_thing_status"] == ("GET", "things/status")
    # a second confirms the GET derivation generalizes
    assert eps["get_thing_about"] == ("GET", "things/about")
    via = next(e.via for e in client.endpoints if e.method_name == "get_thing_status")
    assert via == "self.rest.get"


def test_derived_state_keywords_reuse_populating_endpoint(make_robot_suite):
    """Defect A regression: load_thing_groups / get_cached_things do NOT make
    their own HTTP call — they return state populated by the
    `thing_group_flow` property (GET things/{thing_id}/flow). The extractor must
    classify them as DERIVED and carry the populating endpoint, so the converter
    reuses it instead of guessing a new path (which the user observed 404-ing)."""
    suite = make_robot_suite()
    text = suite.client_py.read_text(encoding="utf-8", errors="replace")
    client = extract_endpoints_from_source(text, service_hint="widgets")
    by_name = {e.method_name: e for e in client.endpoints}

    # The populating property itself is the real direct HTTP call.
    assert "thing_group_flow" in by_name
    assert not by_name["thing_group_flow"].is_derived
    assert by_name["thing_group_flow"].http_method == "GET"
    assert by_name["thing_group_flow"].path == "things/{thing_id}/flow"

    # get_cached_things is DERIVED, referencing the populating member, and carries
    # that member's REAL endpoint — NOT a guessed path.
    gct = by_name["get_cached_things"]
    assert gct.is_derived
    assert gct.derived_from == "thing_group_flow"
    assert (gct.http_method, gct.path) == ("GET", "things/{thing_id}/flow")

    # load_thing_groups is likewise derived from the same populating member.
    ltg = by_name["load_thing_groups"]
    assert ltg.is_derived
    assert ltg.derived_from == "thing_group_flow"
    assert (ltg.http_method, ltg.path) == ("GET", "things/{thing_id}/flow")

    # Crucially, no derived endpoint collapses to a bare /things guess.
    assert gct.path != "things"


def test_derived_state_classification_synthetic():
    """A public keyword that delegates to another member (no direct HTTP call
    of its own) is classified DERIVED. When the populating member's path is
    known it is carried; when unknown, the endpoint is left blank so the
    converter skips-with-reason rather than guessing."""
    src = '''
class Svc:
    def list_things(self):
        return self.rest.get("things")

    @property
    def _cache(self):
        return self.rest.get("things/{id}")

    def get_cached(self):
        return self._cache["payload"]

    def get_unknown(self):
        return self.helper()
'''
    client = extract_endpoints_from_source(src)
    by_name = {e.method_name: e for e in client.endpoints}

    # A public direct call qualifies the class as a REST client.
    assert client.is_rest_client
    assert by_name["list_things"].http_method == "GET"

    # Delegates to a member with a known direct call -> carries that endpoint.
    assert by_name["get_cached"].is_derived
    assert by_name["get_cached"].derived_from == "_cache"
    assert (by_name["get_cached"].http_method, by_name["get_cached"].path) == ("GET", "things/{id}")

    # Delegates to a member with NO resolvable endpoint -> derived, blank path.
    assert by_name["get_unknown"].is_derived
    assert by_name["get_unknown"].http_method == ""
    assert by_name["get_unknown"].path == ""


# ──────────────────────────────────────────────────────────────────────
# build_endpoint_context — consumes an analyzer-shaped analysis dict
# ──────────────────────────────────────────────────────────────────────

def test_build_context_from_analysis_shape(make_robot_suite):
    suite = make_robot_suite()
    analysis = {
        "source_root": str(suite.source_root),
        "files": [
            {
                "file_path": "suite.robot",
                "settings": {
                    "libraries": [
                        {
                            "raw_module": "widgets.api.client.Client",
                            "alias": "Client",
                            "args": ["widgets"],
                            "resolved_path": str(suite.client_py),
                            "exists": True,
                            "kind": "custom",
                        }
                    ],
                },
                "test_cases": [],
            }
        ],
    }
    context = build_endpoint_context(analysis, str(suite.source_root))
    assert context
    # endpoint map header + native-translation instruction
    assert "endpoint map" in context.lower()
    assert "APIRequestContext" in context
    # derived status mapping + base-url pieces
    assert "get_thing_status" in context
    assert "GET /widgets-api/things/status" in context
    assert "port=`8080`" in context
    assert "service/host=`widgets`" in context
    # resolved source included verbatim for header/auth reference — assert on
    # content known to be near the top: the class + its __init__ defaults.
    assert "Resolved custom-library source" in context
    assert "class Client" in context
    assert 'base_path="/widgets-api"' in context

    # Defect A: the "never guess" rule and the derived-state reuse guidance are
    # part of the injected map, and get_cached_things points at the REAL
    # populating endpoint (not a guessed /things).
    assert "NEVER invent" in context
    assert "DERIVED" in context
    assert "get_cached_things" in context
    assert "GET /widgets-api/things/{thing_id}/flow" in context
    # the derived path appears in the DISTILLED map (not only the verbatim
    # source block), so it survives the prompt's shared-context cap.
    map_only = context.split("## Resolved custom-library source")[0]
    assert "things/{thing_id}/flow" in map_only


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
                            "raw_module": "widgets.api.client.Client",
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
