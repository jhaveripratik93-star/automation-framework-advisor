"""Generate sample ZIP datasets for the Repo Comparator feature.

Repo A: K6 (JavaScript)      — 5 test cases across 3 files + resources/ + clamps/
Repo B: Robot Framework      — 5 matching test cases across 3 files + resources/ + clamps/
Repo C: Playwright (Python)  — 5 matching test cases across 3 files + resources/ + clamps/
Repo D: Selenium (Python)    — 5 test cases matching K6 assertion counts

Assertion counts:
  TC1 - Login with valid credentials   K6:2  RF:2  PW:2  SE:2  all MATCH
  TC2 - Get user profile               K6:4  RF:2  PW:1  SE:4  PW/RF MISMATCH (for-loop assertions in K6/SE)
  TC3 - Create new order               K6:6  RF:3  PW:3  SE:6  RF/PW MISMATCH (for-loop assertions in K6/SE)
  TC4 - Update user password           K6:3  RF:3  PW:1  SE:3  PW MISMATCH
  TC5 - Delete order                   K6:2  RF:2  PW:2  SE:2  all MATCH
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

    # ── tests/auth_tests.js  (TC1:2, TC2:4) ─────────────────────────
    "repo_a_k6/tests/auth_tests.js": """\
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL } from '../resources/config.js';
import { getAuthHeaders } from '../clamps/helpers.js';

export const options = { vus: 1, duration: '10s' };

export default function () {

  // TC1: login with valid credentials
  const loginRes = http.post(`${BASE_URL}/api/auth/login`, JSON.stringify({
    username: 'admin',
    password: 'secret123',
  }), { headers: { 'Content-Type': 'application/json' } });

  check(loginRes, {
    'dashboard in redirect url': (r) => r.headers['Location'] !== undefined && r.headers['Location'].includes('dashboard'),
    'welcome-msg visible':       (r) => r.json().welcome_msg !== undefined,
  });
  sleep(1);

  const token = loginRes.json().token;
  // TC2: get user profile
  const profileRes = http.get(`${BASE_URL}/api/users/me`, {
    headers: getAuthHeaders(token),
  });

  check(profileRes, {
    'profile-card visible': (r) => r.json().profile_card !== undefined,
    'username matches':     (r) => r.json().username === 'admin',
  });

  const roles = profileRes.json().roles || ['user'];
  for (const role of roles) {
    check(role, {
      'role is non-empty string': (r) => typeof r === 'string' && r.length > 0,
      'role is known value':      (r) => ['admin', 'user', 'viewer'].includes(r),
    });
    break; // assert once to keep count deterministic (2 loop assertions)
  }
  sleep(1);
}
""",

    # ── tests/order_tests.js  (TC3:6, TC5:2) ────────────────────────
    "repo_a_k6/tests/order_tests.js": """\
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL } from '../resources/config.js';
import { getAuthHeaders } from '../clamps/helpers.js';

export const options = { vus: 1, duration: '10s' };

export default function () {

  const token = 'test-token-123';

  // TC3: create new order
  const createRes = http.post(`${BASE_URL}/api/orders`, JSON.stringify({
    product_id: 42,
    quantity: 2,
  }), { headers: getAuthHeaders(token) });

  check(createRes, {
    'confirmation in redirect url': (r) => r.headers['Location'] !== undefined && r.headers['Location'].includes('confirmation'),
    'order-id visible':             (r) => r.json().order_id !== undefined,
    'order-quantity matches':       (r) => String(r.json().quantity) === '2',
  });

  const items = createRes.json().items || [{ sku: 'SKU-42', price: 9.99, in_stock: true }];
  for (const item of items) {
    check(item, {
      'item sku is a string':   (i) => typeof i.sku === 'string' && i.sku.length > 0,
      'item price is positive': (i) => i.price > 0,
      'item is in stock':       (i) => i.in_stock === true,
    });
    break; // assert once to keep count deterministic (3 loop assertions)
  }
  sleep(1);

  // TC5: delete order
  const deleteRes = http.del(`${BASE_URL}/api/orders/42`, null, {
    headers: getAuthHeaders(token),
  });

  check(deleteRes, {
    'confirm-dialog visible':       (r) => r.json().confirm_dialog !== undefined,
    'confirm-dialog title matches': (r) => r.json().title === 'Confirm Delete',
  });
  sleep(1);
}
""",

    # ── tests/user_tests.js  (TC4:3) ─────────────────────────────────
    "repo_a_k6/tests/user_tests.js": """\
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL } from '../resources/config.js';
import { getAuthHeaders } from '../clamps/helpers.js';

export const options = { vus: 1, duration: '10s' };

export default function () {

  const token = 'test-token-123';

  // TC4: update user password
  const updateRes = http.put(`${BASE_URL}/api/users/me/password`, JSON.stringify({
    old_password: 'secret123',
    new_password: 'newpass456',
  }), { headers: getAuthHeaders(token) });

  check(updateRes, {
    'success-banner visible':       (r) => r.json().success_banner !== undefined,
    'success-banner text matches':  (r) => r.json().message === 'Password updated',
    'updated-at visible':           (r) => r.json().updated_at !== undefined,
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

# ---------------------------------------------------------------------------
# Repo D — Selenium (Python) — matches K6 assertion counts exactly
# TC1:2  TC2:4  TC3:6  TC4:3  TC5:2
# ---------------------------------------------------------------------------

SE_FILES = {

    # ── tests/test_auth.py  (TC1:2, TC2:4) ─────────────────────────────
    "repo_d_selenium/tests/test_auth.py": """\
import pytest
from selenium.webdriver.common.by import By
from resources.config import BASE_URL
from clamps.helpers import login, get_driver


def test_login_with_valid_credentials():
    # TC1 - 2 assertions
    driver = get_driver()
    login(driver, 'admin', 'secret123')
    assert 'dashboard' in driver.current_url
    assert driver.find_element(By.CLASS_NAME, 'welcome-msg').is_displayed()
    driver.quit()


def test_get_user_profile():
    # TC2 - 4 assertions: 2 base + 2 inside for-loop over roles
    driver = get_driver()
    login(driver, 'admin', 'secret123')
    driver.get(f'{BASE_URL}/profile')
    assert driver.find_element(By.CLASS_NAME, 'profile-card').is_displayed()
    assert driver.find_element(By.ID, 'username').text == 'admin'

    roles = driver.execute_script("return window.__userRoles || ['user']")
    for role in roles:
        assert isinstance(role, str)                  # role is non-empty string
        assert role in ['admin', 'user', 'viewer']    # role is known value
        break  # one iteration only
    driver.quit()
""",

    # ── tests/test_orders.py  (TC3:6, TC5:2) ──────────────────────────
    "repo_d_selenium/tests/test_orders.py": """\
import pytest
from selenium.webdriver.common.by import By
from resources.config import BASE_URL
from clamps.helpers import login, get_driver


def test_create_new_order():
    # TC3 - 6 assertions: 3 base + 3 inside for-loop over order items
    driver = get_driver()
    login(driver, 'admin', 'secret123')
    driver.get(f'{BASE_URL}/orders/new')
    driver.find_element(By.NAME, 'product_id').send_keys('42')
    driver.find_element(By.NAME, 'quantity').send_keys('2')
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    assert 'confirmation' in driver.current_url
    assert driver.find_element(By.CLASS_NAME, 'order-id').is_displayed()
    assert driver.find_element(By.CLASS_NAME, 'order-quantity').text == '2'

    items = driver.execute_script("return window.__orderItems || [{sku: 'SKU-42', price: 9.99, in_stock: true}]")
    for item in items:
        assert isinstance(item['sku'], str)    # item sku is a string
        assert item['price'] > 0               # item price is positive
        assert item['in_stock'] is True        # item is in stock
        break  # one iteration only
    driver.quit()


def test_delete_order():
    # TC5 - 2 assertions
    driver = get_driver()
    login(driver, 'admin', 'secret123')
    driver.get(f'{BASE_URL}/orders/42')
    driver.find_element(By.CSS_SELECTOR, 'button.delete-order').click()
    assert driver.find_element(By.CLASS_NAME, 'confirm-dialog').is_displayed()
    assert driver.find_element(By.CSS_SELECTOR, '.confirm-dialog .title').text == 'Confirm Delete'
    driver.quit()
""",

    # ── tests/test_user.py  (TC4:3) ───────────────────────────────────
    "repo_d_selenium/tests/test_user.py": """\
import pytest
from selenium.webdriver.common.by import By
from resources.config import BASE_URL
from clamps.helpers import login, get_driver


def test_update_user_password():
    # TC4 - 3 assertions (matches K6)
    driver = get_driver()
    login(driver, 'admin', 'secret123')
    driver.get(f'{BASE_URL}/settings/password')
    driver.find_element(By.NAME, 'old_password').send_keys('secret123')
    driver.find_element(By.NAME, 'new_password').send_keys('newpass456')
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    assert driver.find_element(By.CLASS_NAME, 'success-banner').is_displayed()
    assert driver.find_element(By.CLASS_NAME, 'success-banner').text == 'Password updated'
    assert driver.find_element(By.CLASS_NAME, 'updated-at').is_displayed()
    driver.quit()
""",

    # ── resources/config.py ───────────────────────────────────────────────────
    "repo_d_selenium/resources/config.py": """\
import os

BASE_URL = os.getenv('BASE_URL', 'https://app.example.com')
TIMEOUT  = int(os.getenv('TIMEOUT', '10'))
""",

    # ── clamps/helpers.py ──────────────────────────────────────────────────────
    "repo_d_selenium/clamps/helpers.py": """\
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from resources.config import BASE_URL, TIMEOUT


def get_driver():
    opts = Options()
    opts.add_argument('--headless')
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(TIMEOUT)
    return driver


def login(driver, username: str, password: str):
    driver.get(f'{BASE_URL}/login')
    driver.find_element(By.NAME, 'username').send_keys(username)
    driver.find_element(By.NAME, 'password').send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
""",
}

write_zip(SE_FILES, OUT_DIR / "repo_d_selenium.zip")

print("\nDone. Upload these to the Repo Comparator:")
print(f"  Repo A (K6):              {OUT_DIR / 'repo_a_k6.zip'}")
print(f"  Repo B (Robot Framework): {OUT_DIR / 'repo_b_robot.zip'}")
print(f"  Repo C (Playwright):      {OUT_DIR / 'repo_c_playwright.zip'}")
print(f"  Repo D (Selenium):        {OUT_DIR / 'repo_d_selenium.zip'}")
print("\nExpected results (K6 vs Selenium) — all MATCH:")
print("  TC1 Login With Valid Credentials  — K6:2  SE:2  MATCH")
print("  TC2 Get User Profile              — K6:4  SE:4  MATCH (for-loop assertions)")
print("  TC3 Create New Order              — K6:6  SE:6  MATCH (for-loop assertions)")
print("  TC4 Update User Password          — K6:3  SE:3  MATCH")
print("  TC5 Delete Order                  — K6:2  SE:2  MATCH")
