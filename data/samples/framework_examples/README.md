# Framework Test Case Examples

One runnable example per framework in the knowledge base.

## UI / E2E Automation

| File | Framework | Language | Run Command |
|------|-----------|----------|-------------|
| `playwright_example.py` | Playwright | Python | `pytest playwright_example.py` |
| `selenium_example.py` | Selenium | Python | `pytest selenium_example.py` |
| `cypress_example.js` | Cypress | JavaScript | `npx cypress run` |
| `webdriverio_example.js` | WebdriverIO | JavaScript | `npx wdio run wdio.conf.js` |
| `puppeteer_example.js` | Puppeteer | JavaScript | `npx jest puppeteer_example.test.js` |
| `testcafe_example.js` | TestCafe | JavaScript | `npx testcafe chrome testcafe_example.js` |
| `robot_framework_example.robot` | Robot Framework | Robot DSL | `robot robot_framework_example.robot` |

## Mobile Automation

| File | Framework | Language | Run Command |
|------|-----------|----------|-------------|
| `appium_example.py` | Appium | Python | `pytest appium_example.py` |

## API Testing

| File | Framework | Language | Run Command |
|------|-----------|----------|-------------|
| `karate_example.feature` | Karate | Gherkin | `mvn test` |
| `rest_assured_example.java` | REST Assured | Java | `mvn test` |

## Performance / Load Testing

| File | Framework | Language | Run Command |
|------|-----------|----------|-------------|
| `k6_example.js` | K6 | JavaScript | `k6 run k6_example.js` |
| `locust_example.py` | Locust | Python | `locust -f locust_example.py` |

## Infrastructure as Code

| File | Framework | Language | Run Command |
|------|-----------|----------|-------------|
| `terraform_example.tf` | Terraform | HCL | `terraform apply` |
| `ansible_example.yml` | Ansible | YAML | `ansible-playbook ansible_example.yml` |
| `pulumi_example.py` | Pulumi | Python | `pulumi up` |
| `chef_example.rb` | Chef | Ruby | `chef-client --local-mode chef_example.rb` |
| `cloudformation_example.yml` | CloudFormation | YAML | `aws cloudformation deploy ...` |

## Common Test Scenario

All UI/API examples test the same scenario: **login with valid credentials**.
- Username: `admin`
- Password: `password123`
- Expected: redirect to `/dashboard` with heading "Dashboard"

This makes it easy to compare syntax and structure across frameworks side-by-side.
