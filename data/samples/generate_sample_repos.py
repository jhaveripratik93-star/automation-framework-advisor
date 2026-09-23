"""Generate sample ZIP datasets for the Repo Comparator feature.

Repo A: K6 (JavaScript)      — 5 test cases across 3 files + resources/ + clamps/
Repo B: Robot Framework      — 5 matching test cases across 3 files + resources/ + clamps/
Repo C: Playwright (Python)  — 5 matching test cases across 3 files + resources/ + clamps/

Assertion counts:
  TC1 - Login with valid credentials   K6:2  RF:2  PW:2   all MATCH
  TC2 - Get user profile               K6:2  RF:2  PW:1   PW MISMATCH (missing username check)
  TC3 - Create new order               K6:3  RF:3  PW:3   all MATCH
  TC4 - Update user password           K6:3  RF:3  PW:1   PW MISMATCH vs K6/RF
  TC5 - Delete order                   K6:2  RF:2  PW:2   all MATCH
"""
import io
import zipfile
from pathlib import Path

OUT_DIR = Path("data/samples")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Repo A — K6
# ---------------------------------------------------------------------------

K6_FILES = {

    # ── tests/auth_tests.js  (TC1, TC2) ──────────────────────────────
    "repo_a_k6/tests/auth_tests.js": """\
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL } from '../resources/config.js';
import { getAuthHeaders } from '../clamps/helpers.js';

export const options = { vus: 1, duration: '10s' };

export default function () {

  // TC1: Login with valid credentials
  const loginRes = http.post(`${BASE_URL}/api/auth/login`, JSON.stringify({
    username: 'admin',
    password: 'secret123',
  }), { headers: { 'Content-Type': 'application/json' } });

  check(loginRes, {
    'login status is 200':  (r) => r.status === 200,
    'token present':        (r) => r.json().token !== undefined,
  });
  sleep(1);

  // TC2: Get user profile
  const token = loginRes.json().token;
  // TC2: Get user profile
  const profileRes = http.get(`${BASE_URL}/api/users/me`, {
    headers: getAuthHeaders(token),
  });

  check(profileRes, {
    'profile status is 200': (r) => r.status === 200,
    'username matches':      (r) => r.json().username === 'admin',
  });
  sleep(1);
}
""",

    # ── tests/order_tests.js  (TC3, TC5) ─────────────────────────────
    "repo_a_k6/tests/order_tests.js": """\
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL } from '../resources/config.js';
import { getAuthHeaders } from '../clamps/helpers.js';

export const options = { vus: 1, duration: '10s' };

export default function () {

  const token = 'test-token-123';

  // TC3: Create new order
  const createRes = http.post(`${BASE_URL}/api/orders`, JSON.stringify({
    product_id: 42,
    quantity: 2,
  }), { headers: getAuthHeaders(token) });

  check(createRes, {
    'create order status 201': (r) => r.status === 201,
    'order id present':        (r) => r.json().order_id !== undefined,
    'quantity matches':        (r) => r.json().quantity === 2,
  });
  sleep(1);

  // TC5: Delete order
  const deleteRes = http.del(`${BASE_URL}/api/orders/42`, null, {
    headers: getAuthHeaders(token),
  });

  check(deleteRes, {
    'delete status 204':    (r) => r.status === 204,
    'body is empty':        (r) => r.body === null || r.body === '',
  });
  sleep(1);
}
""",

    # ── tests/user_tests.js  (TC4) ────────────────────────────────────
    "repo_a_k6/tests/user_tests.js": """\
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL } from '../resources/config.js';
import { getAuthHeaders } from '../clamps/helpers.js';

export const options = { vus: 1, duration: '10s' };

export default function () {

  const token = 'test-token-123';

  // TC4: Update user password
  const updateRes = http.put(`${BASE_URL}/api/users/me/password`, JSON.stringify({
    old_password: 'secret123',
    new_password: 'newpass456',
  }), { headers: getAuthHeaders(token) });

  check(updateRes, {
    'update status 200':    (r) => r.status === 200,
    'message is success':   (r) => r.json().message === 'Password updated',
    'updated_at present':   (r) => r.json().updated_at !== undefined,
  });
  sleep(1);
}
""",

    # ── resources/config.js ───────────────────────────────────────────
    "repo_a_k6/resources/config.js": """\
// Shared configuration for K6 test suite
export const BASE_URL = __ENV.BASE_URL || 'https://api.example.com';
export const TIMEOUT  = 5000;
""",

    # ── clamps/helpers.js ─────────────────────────────────────────────
    "repo_a_k6/clamps/helpers.js": """\
// Shared helper utilities for K6 tests
export function getAuthHeaders(token) {
  return {
    'Content-Type':  'application/json',
    'Authorization': `Bearer ${token}`,
  };
}

export function assertStatus(res, expected) {
  check(res, {
    [`status is ${expected}`]: (r) => r.status === expected,
  });
}
""",
}

# ---------------------------------------------------------------------------
# Repo B — Robot Framework
# ---------------------------------------------------------------------------

RF_FILES = {

    # ── tests/auth_tests.robot  (TC1, TC2) ───────────────────────────
    "repo_b_robot/tests/auth_tests.robot": """\
*** Settings ***
Library     RequestsLibrary
Library     Collections
Resource    ../resources/common.robot
Resource    ../clamps/keywords.robot

Suite Setup     Create Session    api    ${BASE_URL}

*** Variables ***
${BASE_URL}    https://api.example.com

*** Test Cases ***
Login With Valid Credentials
    [Documentation]    TC1 - Verify login returns token
    ${body}=    Create Dictionary    username=admin    password=secret123
    ${response}=    POST On Session    api    /api/auth/login    json=${body}
    Status Should Be    200    ${response}
    ${json}=    Set Variable    ${response.json()}
    Dictionary Should Contain Key    ${json}    token

Get User Profile
    [Documentation]    TC2 - Verify authenticated user profile
    ${headers}=    Get Auth Headers    test-token-123
    ${response}=    GET On Session    api    /api/users/me    headers=${headers}
    Status Should Be    200    ${response}
    ${json}=    Set Variable    ${response.json()}
    Should Be Equal As Strings    ${json}[username]    admin
""",

    # ── tests/order_tests.robot  (TC3, TC5) ──────────────────────────
    "repo_b_robot/tests/order_tests.robot": """\
*** Settings ***
Library     RequestsLibrary
Library     Collections
Resource    ../resources/common.robot
Resource    ../clamps/keywords.robot

Suite Setup     Create Session    api    ${BASE_URL}

*** Variables ***
${BASE_URL}    https://api.example.com
${TOKEN}       test-token-123

*** Test Cases ***
Create New Order
    [Documentation]    TC3 - Verify order creation returns 201 with order details
    ${headers}=    Get Auth Headers    ${TOKEN}
    ${body}=    Create Dictionary    product_id=${42}    quantity=${2}
    ${response}=    POST On Session    api    /api/orders    json=${body}    headers=${headers}
    Status Should Be    201    ${response}
    ${json}=    Set Variable    ${response.json()}
    Dictionary Should Contain Key    ${json}    order_id
    Should Be Equal As Integers    ${json}[quantity]    2

Delete Order
    [Documentation]    TC5 - Verify order deletion returns 204
    ${headers}=    Get Auth Headers    ${TOKEN}
    ${response}=    DELETE On Session    api    /api/orders/42    headers=${headers}
    Status Should Be    204    ${response}
    Should Be Equal As Strings    ${response.text}    ${EMPTY}
""",

    # ── tests/user_tests.robot  (TC4 — intentional mismatch: 3 assertions vs K6's 2) ──
    "repo_b_robot/tests/user_tests.robot": """\
*** Settings ***
Library     RequestsLibrary
Library     Collections
Resource    ../resources/common.robot
Resource    ../clamps/keywords.robot

Suite Setup     Create Session    api    ${BASE_URL}

*** Variables ***
${BASE_URL}    https://api.example.com
${TOKEN}       test-token-123

*** Test Cases ***
Update User Password
    [Documentation]    TC4 - Verify password update (RF has extra assertion vs K6)
    ${headers}=    Get Auth Headers    ${TOKEN}
    ${body}=    Create Dictionary    old_password=secret123    new_password=newpass456
    ${response}=    PUT On Session    api    /api/users/me/password    json=${body}    headers=${headers}
    Status Should Be    200    ${response}
    ${json}=    Set Variable    ${response.json()}
    Should Be Equal As Strings    ${json}[message]    Password updated
    Should Not Be Empty    ${json}[updated_at]
""",

    # ── resources/common.robot ────────────────────────────────────────
    "repo_b_robot/resources/common.robot": """\
*** Settings ***
Library    RequestsLibrary
Library    Collections

*** Variables ***
${BASE_URL}    https://api.example.com
${TIMEOUT}     5
""",

    # ── clamps/keywords.robot ─────────────────────────────────────────
    "repo_b_robot/clamps/keywords.robot": """\
*** Settings ***
Library    Collections

*** Keywords ***
Get Auth Headers
    [Arguments]    ${token}
    ${headers}=    Create Dictionary
    ...    Content-Type=application/json
    ...    Authorization=Bearer ${token}
    RETURN    ${headers}

Assert Status
    [Arguments]    ${response}    ${expected_status}
    Status Should Be    ${expected_status}    ${response}
""",
}

# ---------------------------------------------------------------------------
# Write ZIPs
# ---------------------------------------------------------------------------

def write_zip(files: dict, zip_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in files.items():
            zf.writestr(arcname, content)
    zip_path.write_bytes(buf.getvalue())
    print(f"Created: {zip_path}  ({len(files)} files)")


write_zip(K6_FILES,  OUT_DIR / "repo_a_k6.zip")
write_zip(RF_FILES,  OUT_DIR / "repo_b_robot.zip")

# ---------------------------------------------------------------------------
# Repo C — Playwright (Python)
# ---------------------------------------------------------------------------

PW_FILES = {

    # ── tests/test_auth.py  (TC1, TC2) ──────────────────────────────────
    "repo_c_playwright/tests/test_auth.py": (
        "from playwright.sync_api import Page, expect\n"
        "from resources.config import BASE_URL\n"
        "from clamps.helpers import get_auth_headers\n"
        "\n"
        "\n"
        "def test_login_with_valid_credentials(page: Page):\n"
        "    # TC1 - Verify login returns token (2 assertions)\n"
        "    page.goto(f'{BASE_URL}/login')\n"
        "    page.fill(\"input[name='username']\", 'admin')\n"
        "    page.fill(\"input[name='password']\", 'secret123')\n"
        "    page.click(\"button[type='submit']\")\n"
        "    expect(page).to_have_url(f'{BASE_URL}/dashboard')\n"
        "    expect(page.locator('.welcome-msg')).to_be_visible()\n"
        "\n"
        "\n"
        "def test_get_user_profile(page: Page):\n"
        "    # TC2 - Verify user profile (1 assertion - MISMATCH vs K6:2 RF:2)\n"
        "    page.goto(f'{BASE_URL}/profile')\n"
        "    # username assertion intentionally omitted to create mismatch\n"
        "    expect(page.locator('.profile-card')).to_be_visible()\n"
    ),

    # ── tests/test_orders.py  (TC3, TC5) ─────────────────────────────
    "repo_c_playwright/tests/test_orders.py": (
        "from playwright.sync_api import Page, expect\n"
        "from resources.config import BASE_URL\n"
        "from clamps.helpers import get_auth_headers\n"
        "\n"
        "\n"
        "def test_create_new_order(page: Page):\n"
        "    # TC3 - Verify order creation (3 assertions - MATCH)\n"
        "    page.goto(f'{BASE_URL}/orders/new')\n"
        "    page.fill(\"input[name='product_id']\", '42')\n"
        "    page.fill(\"input[name='quantity']\", '2')\n"
        "    page.click(\"button[type='submit']\")\n"
        "    expect(page).to_have_url(f'{BASE_URL}/orders/confirmation')\n"
        "    expect(page.locator('.order-id')).to_be_visible()\n"
        "    expect(page.locator('.order-quantity')).to_have_text('2')\n"
        "\n"
        "\n"
        "def test_delete_order(page: Page):\n"
        "    # TC5 - Verify order deletion (2 assertions - MATCH)\n"
        "    page.goto(f'{BASE_URL}/orders/42')\n"
        "    page.click('button.delete-order')\n"
        "    expect(page.locator('.confirm-dialog')).to_be_visible()\n"
        "    expect(page.locator('.confirm-dialog .title')).to_have_text('Confirm Delete')\n"
    ),

    # ── tests/test_user.py  (TC4) ─────────────────────────────────────
    "repo_c_playwright/tests/test_user.py": (
        "from playwright.sync_api import Page, expect\n"
        "from resources.config import BASE_URL\n"
        "from clamps.helpers import get_auth_headers\n"
        "\n"
        "\n"
        "def test_update_user_password(page: Page):\n"
        "    # TC4 - Verify password update (1 assertion - MISMATCH vs K6:2 RF:3)\n"
        "    page.goto(f'{BASE_URL}/settings/password')\n"
        "    page.fill(\"input[name='old_password']\", 'secret123')\n"
        "    page.fill(\"input[name='new_password']\", 'newpass456')\n"
        "    page.click(\"button[type='submit']\")\n"
        "    # success message and updated_at assertions intentionally omitted\n"
        "    expect(page.locator('.success-banner')).to_be_visible()\n"
    ),

    # ── resources/config.py ───────────────────────────────────────────────
    "repo_c_playwright/resources/config.py": (
        "import os\n"
        "\n"
        "BASE_URL = os.getenv('BASE_URL', 'https://app.example.com')\n"
        "TIMEOUT  = int(os.getenv('TIMEOUT', '5000'))\n"
    ),

    # ── clamps/helpers.py ─────────────────────────────────────────────────
    "repo_c_playwright/clamps/helpers.py": (
        "def get_auth_headers(token: str) -> dict:\n"
        "    return {\n"
        "        'Content-Type':  'application/json',\n"
        "        'Authorization': f'Bearer {token}',\n"
        "    }\n"
    ),
}

write_zip(PW_FILES, OUT_DIR / "repo_c_playwright.zip")

print("\nDone. Upload these to the Repo Comparator:")
print(f"  Repo A (K6):             {OUT_DIR / 'repo_a_k6.zip'}")
print(f"  Repo B (Robot Framework): {OUT_DIR / 'repo_b_robot.zip'}")
print(f"  Repo C (Playwright):      {OUT_DIR / 'repo_c_playwright.zip'}")
print("\nExpected results (K6 vs Playwright):")
print("  TC1 Login With Valid Credentials  — K6:2  PW:2  MATCH")
print("  TC2 Get User Profile              — K6:2  PW:1  MISMATCH (PW missing username check)")
print("  TC3 Create New Order              — K6:3  PW:3  MATCH")
print("  TC4 Update User Password          — K6:2  PW:1  MISMATCH (PW missing 2 assertions)")
print("  TC5 Delete Order                  — K6:2  PW:2  MATCH")
