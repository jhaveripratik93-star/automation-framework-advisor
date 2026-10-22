"""Prerequisites automation patterns and script generation."""
from __future__ import annotations

# Pre-requisite automation patterns
PREREQ_PATTERNS = {
    "database": {
        "keywords": ["database", "db", "sql", "mysql", "postgres", "mongodb", "seed", "schema"],
        "automation": "python",
        "script_type": "DB setup script",
        "tools": ["sqlalchemy", "psycopg2", "pymongo", "alembic"],
        "cicd_step": "pre-test",
        "example": "python scripts/db_setup.py --seed"
    },
    "environment": {
        "keywords": ["env", "environment", "config", "variable", ".env", "secrets"],
        "automation": "shell",
        "script_type": "Environment config script",
        "tools": ["python-dotenv", "envsubst", "aws-ssm"],
        "cicd_step": "pre-test",
        "example": "source scripts/setup_env.sh"
    },
    "service": {
        "keywords": ["service", "server", "start", "docker", "container", "microservice"],
        "automation": "shell",
        "script_type": "Service startup script",
        "tools": ["docker-compose", "docker", "systemctl"],
        "cicd_step": "pre-test",
        "example": "docker-compose up -d"
    },
    "mock": {
        "keywords": ["mock", "stub", "fake", "wiremock", "mockserver"],
        "automation": "python",
        "script_type": "Mock server setup",
        "tools": ["wiremock", "responses", "httpretty", "mockserver"],
        "cicd_step": "pre-test",
        "example": "python scripts/start_mocks.py"
    },
    "data": {
        "keywords": ["test data", "fixture", "sample", "csv", "json", "excel"],
        "automation": "python",
        "script_type": "Test data generator",
        "tools": ["faker", "factory_boy", "pandas"],
        "cicd_step": "pre-test",
        "example": "python scripts/generate_test_data.py"
    },
    "auth": {
        "keywords": ["auth", "login", "token", "oauth", "jwt", "session", "cookie"],
        "automation": "python",
        "script_type": "Auth token generator",
        "tools": ["requests", "pyjwt", "authlib"],
        "cicd_step": "pre-test",
        "example": "python scripts/get_auth_token.py"
    },
    "browser": {
        "keywords": ["browser", "driver", "chromedriver", "geckodriver", "webdriver"],
        "automation": "shell",
        "script_type": "Browser/driver setup",
        "tools": ["webdriver-manager", "playwright install", "npx playwright install"],
        "cicd_step": "setup",
        "example": "npx playwright install --with-deps"
    },
    "mobile": {
        "keywords": ["emulator", "simulator", "device", "android", "ios", "appium"],
        "automation": "shell",
        "script_type": "Mobile emulator setup",
        "tools": ["avdmanager", "xcrun simctl", "appium"],
        "cicd_step": "setup",
        "example": "emulator -avd test_device -no-window &"
    },
    "cleanup": {
        "keywords": ["cleanup", "teardown", "reset", "clear", "delete"],
        "automation": "shell",
        "script_type": "Cleanup script",
        "tools": ["rm", "docker rm", "psql"],
        "cicd_step": "post-test",
        "example": "bash scripts/cleanup.sh"
    },
    "network": {
        "keywords": ["vpn", "proxy", "network", "firewall", "port"],
        "automation": "shell",
        "script_type": "Network config script",
        "tools": ["iptables", "ssh tunnel", "ngrok"],
        "cicd_step": "pre-test",
        "example": "bash scripts/setup_network.sh"
    }
}


def match_prereq_pattern(prereq: dict) -> tuple[str, dict] | None:
    """Match prerequisite to automation pattern."""
    name = prereq.get("name", "").lower()
    desc = prereq.get("description", "").lower()
    manual = prereq.get("manual_steps", "").lower()
    combined = f"{name} {desc} {manual}"
    
    for pattern_name, pattern in PREREQ_PATTERNS.items():
        if any(kw in combined for kw in pattern["keywords"]):
            return (pattern_name, pattern)
    return None


def generate_script(pattern: str, prereq: dict, p: dict) -> str:
    """Generate automation script skeleton based on pattern."""
    name = prereq.get("name", "setup").lower().replace(" ", "_")
    
    if p["automation"] == "python":
        return _generate_python_script(pattern, name, prereq)
    return _generate_shell_script(pattern, name, prereq)


def _generate_python_script(pattern: str, name: str, prereq: dict) -> str:
    """Generate Python script based on pattern."""
    templates = {
        "database": f'''# scripts/{name}_setup.py
import os
from sqlalchemy import create_engine, text

def setup_database():
    db_url = os.getenv("DATABASE_URL", "postgresql://localhost/testdb")
    engine = create_engine(db_url)
    
    # Run migrations/seed
    with engine.connect() as conn:
        conn.execute(text("-- Add your schema/seed SQL here"))
        conn.commit()
    print("[OK] Database setup complete")

if __name__ == "__main__":
    setup_database()''',

        "data": f'''# scripts/{name}_setup.py
import json
from faker import Faker

def generate_test_data(output="data/test_data.json"):
    fake = Faker()
    data = [{{
        "id": i,
        "name": fake.name(),
        "email": fake.email()
    }} for i in range(100)]
    
    with open(output, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[OK] Generated test data: {{output}}")

if __name__ == "__main__":
    generate_test_data()''',

        "auth": f'''# scripts/{name}_setup.py
import os
import requests

def get_auth_token():
    auth_url = os.getenv("AUTH_URL", "http://localhost:8080/auth/token")
    resp = requests.post(auth_url, json={{
        "username": os.getenv("TEST_USER"),
        "password": os.getenv("TEST_PASS")
    }})
    token = resp.json().get("access_token")
    
    # Save for tests to use
    with open(".auth_token", "w") as f:
        f.write(token)
    print("[OK] Auth token saved")
    return token

if __name__ == "__main__":
    get_auth_token()''',

        "mock": f'''# scripts/{name}_setup.py
import subprocess
import time

def start_mock_server(port=8081):
    # Start WireMock or similar
    proc = subprocess.Popen([
        "java", "-jar", "wiremock.jar",
        "--port", str(port),
        "--root-dir", "mocks/"
    ])
    time.sleep(3)  # Wait for startup
    print(f"[OK] Mock server running on port {{port}}")
    return proc

if __name__ == "__main__":
    start_mock_server()'''
    }
    
    return templates.get(pattern, f'''# scripts/{name}_setup.py
import os

def setup():
    # TODO: Implement {prereq.get("description", "setup")}
    print("[OK] {name} setup complete")

if __name__ == "__main__":
    setup()''')


def _generate_shell_script(pattern: str, name: str, prereq: dict) -> str:
    """Generate Shell script based on pattern."""
    templates = {
        "service": f'''#!/bin/bash
# scripts/{name}_setup.sh
set -e

echo "Starting services..."
docker-compose -f docker-compose.test.yml up -d

# Wait for services to be healthy
echo "Waiting for services..."
sleep 10
docker-compose ps

echo "[OK] Services ready"''',

        "environment": f'''#!/bin/bash
# scripts/{name}_setup.sh
set -e

# Load environment variables
if [ -f .env.test ]; then
    export $(cat .env.test | xargs)
fi

# Or fetch from secrets manager
# aws ssm get-parameters --names "/test/config" --with-decryption

echo "[OK] Environment configured"''',

        "browser": f'''#!/bin/bash
# scripts/{name}_setup.sh
set -e

# Install browser dependencies
npx playwright install --with-deps chromium firefox

# Or for Selenium
# pip install webdriver-manager

echo "[OK] Browsers installed"''',

        "mobile": f'''#!/bin/bash
# scripts/{name}_setup.sh
set -e

# Start Android emulator
emulator -avd test_device -no-window -no-audio &
adb wait-for-device
adb shell input keyevent 82  # Unlock

echo "[OK] Emulator ready"''',

        "cleanup": f'''#!/bin/bash
# scripts/{name}_cleanup.sh
set -e

# Stop services
docker-compose down -v

# Clean test artifacts
rm -rf test-results/ .auth_token

echo "[OK] Cleanup complete"'''
    }
    
    return templates.get(pattern, f'''#!/bin/bash
# scripts/{name}_setup.sh
set -e

# TODO: Implement {prereq.get("description", "setup")}
echo "[OK] {name} setup complete"''')
