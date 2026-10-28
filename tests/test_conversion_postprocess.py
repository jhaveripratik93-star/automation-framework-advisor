"""Tests for src/conversion/postprocess.py — the deterministic browser-fixture
guard. Pure, no LLM, no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.conversion.postprocess import strip_unused_browser_fixture


# (a) unused page removed, other params kept ------------------------------

def test_removes_unused_page_keeps_other_param():
    code = (
        "from playwright.sync_api import Page\n"
        "\n"
        "def test_x(page: Page, nifi):\n"
        "    status = nifi.get_flow_status()\n"
        "    assert 'controllerStatus' in status\n"
    )
    out = strip_unused_browser_fixture(code)
    assert "def test_x(nifi):" in out
    assert "page" not in out.split("def test_x")[1].split(":")[0]
    assert "from playwright.sync_api import Page" not in out
    # body untouched
    assert "status = nifi.get_flow_status()" in out
    assert "assert 'controllerStatus' in status" in out


def test_removes_page_as_first_of_three():
    code = (
        "def test_x(page: Page, a, b):\n"
        "    return a + b\n"
    )
    out = strip_unused_browser_fixture(code)
    assert "def test_x(a, b):" in out


def test_removes_page_as_middle_param():
    code = (
        "def test_x(a, page: Page, b):\n"
        "    return a + b\n"
    )
    out = strip_unused_browser_fixture(code)
    assert "def test_x(a, b):" in out


def test_removes_page_as_sole_param():
    code = (
        "def test_x(page: Page):\n"
        "    assert 1 == 1\n"
    )
    out = strip_unused_browser_fixture(code)
    assert "def test_x():" in out


# (b) real UI test left intact (no false positive) ------------------------

def test_leaves_ui_test_using_goto_and_locator():
    code = (
        "from playwright.sync_api import Page, expect\n"
        "\n"
        "def test_login(page: Page):\n"
        "    page.goto('https://example.com')\n"
        "    page.get_by_role('button').click()\n"
        "    expect(page.get_by_role('heading')).to_be_visible()\n"
    )
    out = strip_unused_browser_fixture(code)
    assert out == code


def test_leaves_mixed_file_untouched_if_any_browser_usage():
    # One test uses page genuinely; a conservative guard must not touch the
    # other unused-page test in the same file.
    code = (
        "from playwright.sync_api import Page\n"
        "\n"
        "def test_api(page: Page, nifi):\n"
        "    assert nifi.get_flow_status()\n"
        "\n"
        "def test_ui(page: Page):\n"
        "    page.goto('https://example.com')\n"
    )
    out = strip_unused_browser_fixture(code)
    assert out == code


# (c) idempotency ----------------------------------------------------------

def test_idempotent():
    code = (
        "from playwright.sync_api import Page\n"
        "\n"
        "def test_x(page: Page, nifi):\n"
        "    assert nifi.ok()\n"
    )
    once = strip_unused_browser_fixture(code)
    twice = strip_unused_browser_fixture(once)
    assert once == twice


def test_nothing_to_do_returns_unchanged():
    code = (
        "def test_plain(nifi):\n"
        "    assert nifi.ok()\n"
    )
    assert strip_unused_browser_fixture(code) == code


# (d) import pruning -------------------------------------------------------

def test_prunes_only_page_when_expect_still_used():
    code = (
        "from playwright.sync_api import Page, expect\n"
        "\n"
        "def test_x(page: Page, client):\n"
        "    result = client.fetch()\n"
        "    expect(result).not_to_be_none()\n"  # expect on a value, not a page
    )
    out = strip_unused_browser_fixture(code)
    assert "from playwright.sync_api import expect" in out
    assert "Page" not in out.split("\n")[0]


def test_removes_import_line_when_both_unused():
    code = (
        "from playwright.sync_api import Page, expect\n"
        "\n"
        "def test_x(page: Page, client):\n"
        "    assert client.fetch()\n"
    )
    out = strip_unused_browser_fixture(code)
    assert "from playwright.sync_api import" not in out
    assert "def test_x(client):" in out


def test_keeps_other_playwright_imports():
    code = (
        "from playwright.sync_api import Page, sync_playwright\n"
        "\n"
        "def test_x(page: Page, client):\n"
        "    with sync_playwright() as p:\n"
        "        assert p is not None\n"
        "    assert client.fetch()\n"
    )
    out = strip_unused_browser_fixture(code)
    assert "sync_playwright" in out
    assert "Page" not in out.split("\n")[0]


# (e) fixture mirroring the user's converted nifi file --------------------

NIFI_CONVERTED = """from playwright.sync_api import Page
import pytest
from clamps.nifi.nifi import Nifi


@pytest.fixture
def nifi() -> Nifi:
    return Nifi()


@pytest.mark.nifi
def test_can_get_flow_status(page: Page, nifi: Nifi):
    status = nifi.get_flow_status()
    assert "controllerStatus" in status


@pytest.mark.nifi
def test_can_get_flow_about(page: Page, nifi: Nifi):
    about = nifi.get_flow_about()
    assert "about" in about


@pytest.mark.nifi
def test_can_get_flow_process_groups(page: Page, nifi: Nifi):
    nifi.load_flow_process_groups()
    data = nifi.get_flow_data()
    assert data


@pytest.mark.nifi
def test_restarting_nifi_pod(page: Page, nifi: Nifi):
    before = nifi.get_templates()
    after = nifi.get_templates()
    assert before == after
"""


def test_nifi_file_all_four_signatures_lose_page():
    out = strip_unused_browser_fixture(NIFI_CONVERTED)
    assert "from playwright.sync_api import Page" not in out
    for fn in (
        "test_can_get_flow_status",
        "test_can_get_flow_about",
        "test_can_get_flow_process_groups",
        "test_restarting_nifi_pod",
    ):
        assert f"def {fn}(nifi: Nifi):" in out, f"{fn} should keep only nifi"
    # all assertions preserved
    assert out.count("assert ") == NIFI_CONVERTED.count("assert ")
    # custom client import and fixture preserved
    assert "from clamps.nifi.nifi import Nifi" in out
    assert "def nifi() -> Nifi:" in out


# (f) un-parseable input returned unchanged -------------------------------

def test_unparseable_input_unchanged():
    broken = "def test_x(page: Page:\n    this is not python\n"
    assert strip_unused_browser_fixture(broken) == broken
