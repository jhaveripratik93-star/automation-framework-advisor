"""Shared synthetic-fixture builders for the conversion / bundler tests.

Historically several tests pointed at a real Robot suite the user happened to
extract next to the repo (``autotest_2_extracted/.../nifi.robot`` and its
``clamps`` custom-library tree). That path does not exist on a clean checkout
or in CI, so those tests silently skipped and proved nothing.

The ``make_robot_suite`` fixture here builds an equivalent, framework-neutral
suite under pytest's ``tmp_path`` so the SAME real code paths
(``analyze_file`` / ``analyze_path`` / ``_analyze_path_for_studio`` /
``_collect_dependency_files`` / ``build_endpoint_context`` / ``convert``) run
everywhere, against a fixture the test itself created.

The synthetic tree deliberately reproduces every behaviour the old nifi tree
exercised:

  * direct resolved custom-library bundling
      -> ``widgets/api/client.py`` (the ``Client`` library)
  * a second custom library with a ctor arg + alias
      -> ``widgets/infra/k8s/app_controller.py`` (the ``Controller`` library)
  * transitive project-import bundling (``from X import Y`` submodule)
      -> ``client.py`` imports ``common_xyz`` / ``configs.common_config`` / ``settings``
  * package ``__init__.py`` eager-import scanning (the old ``clamps/__init__`` case)
      -> ``widgets/__init__.py`` eagerly imports ``widgets.subapi`` (a submodule)
  * deep fallback-only transitive dep (old application_controller -> kubernetes_base)
      -> ``app_controller.py`` does ``from widgets.infra.k8s.k8s_base import K8sBase``
  * third-party requirement detection, incl. the import!=distribution mapping
      -> real third-party ``import somepkg`` + the ``yaml``->``PyYAML`` mapping
  * endpoint extraction / native-translation context injection
      -> ``Client.get_thing_status`` does ``self.rest.get("things/status")``
  * a derived-state keyword that must NOT be given a guessed endpoint
      -> ``Client.get_cached_things`` returns cached state from a populating member

Nothing here references ``autotest_2_extracted``, ``nifi.robot``, ``clamps`` or
any path outside the repo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- synthetic custom-library source ----------------------------------------

# The primary REST-client custom library. Mirrors the shape the endpoint
# extractor + bundler care about:
#   * a Rest client member built in __init__ with server_port / base_path defaults
#   * public keywords that make literal self.rest.<verb>(<path>) calls
#   * a populating member (property) whose path is known
#   * a DERIVED keyword that returns cached state (no HTTP call of its own)
#   * a project import (common_xyz) -> transitive bundling
#   * a third-party import (somepkg) -> requirements.txt
_CLIENT_PY = '''\
"""A synthetic REST custom library (framework-neutral)."""
import json
import logging

import somepkg  # third-party -> requirements.txt
from common_xyz import helper  # project import -> transitive bundle
from configs.common_config import CommonConfig
from settings import RESOURCE_DIR

LOG = logging.getLogger(__name__)


class Client:
    """Interface for talking to the Widgets service."""

    def __init__(self, host="widgets", server_port=8080, base_path="/widgets-api"):
        self.rest = Rest(host, server_port, base_path)
        self.thing_id = "root"

    @property
    def thing_group_flow(self):
        """Populating member with a known direct call."""
        return self.rest.get("things/{thing_id}/flow", thing_id=self.thing_id)["flow"]

    def get_thing_status(self):
        """GET things/status -> the derived endpoint the prompt must carry."""
        return self.rest.get("things/status")

    def get_thing_about(self):
        return self.rest.get("things/about")

    def load_thing_groups(self):
        """DERIVED: reuses the populating member, no HTTP of its own."""
        return self.thing_group_flow

    def get_cached_things(self):
        """DERIVED: returns cached state populated by thing_group_flow."""
        return self.thing_group_flow["groups"]

    def _private(self):
        return self.rest.get("secret")
'''

# Second custom library, resolved and bundled, that does a deep from-import of a
# sibling module (the fallback-only transitive dependency case) and pulls a
# third-party dep whose import name != distribution name.
_APP_CONTROLLER_PY = '''\
"""A synthetic infra controller custom library."""
import logging

import yaml  # import name yaml -> distribution PyYAML (mapping path)
from fakecloud import api  # third-party -> requirements.txt
from widgets.infra.k8s.k8s_base import K8sBase  # deep project import -> bundle

LOG = logging.getLogger(__name__)


class AppController(K8sBase):
    def __init__(self, app_name):
        self.app_name = app_name

    def restart_app_with_timer(self, minutes):
        return self.restart(self.app_name)
'''

_K8S_BASE_PY = '''\
"""Deep transitive dependency reached only via the controller's from-import."""
import logging

import fakecloud  # third-party -> requirements.txt

LOG = logging.getLogger(__name__)


class K8sBase:
    def restart(self, name):
        return fakecloud.restart(name)
'''

# A submodule eagerly imported by the package __init__ — the "clamps/__init__"
# scan case: directly bundling anything under widgets/ pulls in __init__.py,
# which must be SCANNED so widgets/subapi.py is bundled too.
_SUBAPI_PY = '''\
"""Eagerly imported by widgets/__init__.py; must be bundled via the init scan."""


class SubApi:
    VALUE = 1
'''

_EXTRA_HELPER_PY = '''\
"""A second module eagerly imported by widgets/__init__.py."""

MARKER = "extra"
'''

_COMMON_HELPER_PY = '''\
"""A transitive project dependency of client.py (from common_xyz import helper)."""

HELPER = True
'''

_COMMON_CONFIG_PY = '''\
"""Project config module imported by client.py."""


class CommonConfig:
    pass
'''

_SETTINGS_PY = 'RESOURCE_DIR = "resources"\n'

# Robot suite declaring the two custom libraries (dotted module + trailing
# class), one with WITH NAME + a ctor arg, plus a derived-state keyword call and
# bare BuiltIn keywords.
_SUITE_ROBOT = """\
*** Settings ***
Library      widgets.api.client.Client  WITH NAME  Client
Library    widgets.infra.k8s.app_controller.AppController
...        widgets     WITH NAME  Controller
Force Tags   widgets

*** Test Cases ***
Can Get Thing Status
    [Documentation]  Verify the service is up by fetching the status.
    ${status}=  Client.get_thing_status
    Should Contain  ${status}  state

Can Get Thing About
    [Documentation]  Verify the service is up by fetching the about info.
    ${about}=  Client.get_thing_about
    Should Contain  ${about}  about

Can Get Thing Groups
    [Documentation]  Verify the service is up by fetching its groups.
    Client.load_thing_groups
    ${data}=  Client.get_cached_things
    Should Not Be Empty  ${data}

Restarting The App And Validating Configuration
    [Tags]           Implements_TEST-13556   cenx-platform   dont-run
    ${before}=  Client.get_thing_about
    Controller.restart_app_with_timer   ${5}
    ${after}=  Client.get_thing_about
    Should Be Equal   ${before}       ${after}
"""


# A second, self-contained BuiltIn-only suite so a directory walk sees >1 file.
_EXTRA_SUITE_ROBOT = """\
*** Test Cases ***
Pure Builtin Check
    ${x}=    Set Variable    hi
    Should Be Equal    ${x}    hi

Another Builtin Check
    Should Be Equal    1    1
"""


@dataclass
class SyntheticSuite:
    """Paths into a synthetic Robot suite + custom-library tree under tmp_path."""

    source_root: Path
    suite: Path
    client_py: Path
    app_controller_py: Path
    k8s_base_py: Path
    subapi_py: Path
    common_helper_py: Path
    # Expected in-project dest layout (POSIX) for the primary resolved deps.
    client_dest: str = "widgets/api/client.py"
    app_controller_dest: str = "widgets/infra/k8s/app_controller.py"
    k8s_base_dest: str = "widgets/infra/k8s/k8s_base.py"
    subapi_dest: str = "widgets/subapi.py"
    # The endpoint the status keyword resolves to (base_path + path).
    status_endpoint: str = "GET /widgets-api/things/status"
    # The derived-state populating endpoint (never a guessed /things).
    group_flow_path: str = "things/{thing_id}/flow"
    third_party: tuple[str, ...] = field(
        default_factory=lambda: ("PyYAML", "fakecloud", "somepkg")
    )


def build_synthetic_suite(root: Path) -> SyntheticSuite:
    """Create the full synthetic suite + library tree under ``root`` and return
    a :class:`SyntheticSuite` describing it. ``root`` is the analysis
    source_root the resolver walks from."""
    suite = root / "tests" / "suite.robot"
    _write(suite, _SUITE_ROBOT)

    # A second, fully self-contained (BuiltIn-only) suite in the same dir so a
    # directory walk yields more than one file (the old multi-file nifi case).
    _write(root / "tests" / "extra_suite.robot", _EXTRA_SUITE_ROBOT)

    # widgets package: __init__ eagerly imports a submodule + a helper so the
    # init-scan path pulls them in even when only a deep module is bundled.
    _write(
        root / "widgets" / "__init__.py",
        "from widgets.subapi import SubApi\nfrom widgets.extra.helper import MARKER\n",
    )
    _write(root / "widgets" / "subapi.py", _SUBAPI_PY)
    _write(root / "widgets" / "extra" / "__init__.py", "")
    _write(root / "widgets" / "extra" / "helper.py", _EXTRA_HELPER_PY)

    client_py = root / "widgets" / "api" / "client.py"
    _write(root / "widgets" / "api" / "__init__.py", "")
    _write(client_py, _CLIENT_PY)

    app_controller_py = root / "widgets" / "infra" / "k8s" / "app_controller.py"
    k8s_base_py = root / "widgets" / "infra" / "k8s" / "k8s_base.py"
    _write(root / "widgets" / "infra" / "__init__.py", "")
    _write(root / "widgets" / "infra" / "k8s" / "__init__.py", "")
    _write(app_controller_py, _APP_CONTROLLER_PY)
    _write(k8s_base_py, _K8S_BASE_PY)

    common_helper_py = root / "common_xyz" / "helper.py"
    _write(root / "common_xyz" / "__init__.py", "")
    _write(common_helper_py, _COMMON_HELPER_PY)

    _write(root / "configs" / "__init__.py", "")
    _write(root / "configs" / "common_config.py", _COMMON_CONFIG_PY)

    _write(root / "settings.py", _SETTINGS_PY)

    return SyntheticSuite(
        source_root=root,
        suite=suite,
        client_py=client_py,
        app_controller_py=app_controller_py,
        k8s_base_py=k8s_base_py,
        subapi_py=root / "widgets" / "subapi.py",
        common_helper_py=common_helper_py,
    )


@pytest.fixture
def make_robot_suite(tmp_path):
    """Factory fixture: build a self-contained synthetic Robot suite under
    ``tmp_path`` and return a :class:`SyntheticSuite`.

    Each call creates an isolated source_root so a test can build more than one.
    """
    counter = {"n": 0}

    def _make() -> SyntheticSuite:
        counter["n"] += 1
        root = tmp_path / f"src{counter['n']}"
        root.mkdir(parents=True, exist_ok=True)
        return build_synthetic_suite(root)

    return _make
