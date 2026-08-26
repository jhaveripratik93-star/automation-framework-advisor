# Test Case Coverage Analysis Tool

## Overview

The **Test Case Coverage Analysis Tool** is an LLM-powered tool that analyzes your test cases and generates a comprehensive coverage matrix showing which automation frameworks can handle which test cases. 

This tool helps you make data-driven decisions about framework migration by:
- Mapping test cases to framework capabilities
- Generating coverage percentages
- Identifying gaps and limitations
- Providing recommendations with reasoning

## How It Works

### 1. Tool Definition

The tool is integrated into the agent pipeline's tool executor:
- **Tool Name**: `analyze_test_case_coverage`
- **Location**: `src/tools/executor.py` → `_analyze_test_case_coverage()`
- **Engine**: `src/tools/coverage_engine.py` → `analyze_coverage()` + `render_coverage_report()`
- **Called by**: `ToolSelectionAgent` when user asks about test case coverage

### 2. Input Format

The LLM will parse your natural language request and convert it to the tool call:

```json
{
  "test_cases": [
    {
      "id": "TC001",
      "description": "Verify user login",
      "required_capability": "UI Automation"
    },
    {
      "id": "TC002",
      "description": "Verify REST API response time",
      "required_capability": "API Performance"
    }
  ],
  "frameworks": ["Robot Framework", "Selenium", "k6"]  // Optional
}
```

### 3. Capability Mapping

The tool maps high-level capabilities to framework features:

| Capability | Framework Features |
|------------|-------------------|
| **UI Automation** | ui_testing, browser_testing, web_automation |
| **UI Validation** | ui_testing, visual_regression, accessibility_testing |
| **API Testing** | api_testing, rest_api, graphql_support |
| **API Performance** | performance_testing, load_testing |
| **Performance Testing** | performance_testing, load_testing |
| **File Handling** | file_upload, download_testing |
| **Mobile Testing** | mobile_testing, cross_platform |
| **Custom Library** | plugin_system, extensibility |

### 4. Framework Evaluation

For each test case and framework combination, the tool:
1. Checks if framework has the required capability in its feature set
2. Verifies architecture fit (web, API, mobile, etc.)
3. Checks for limitations that might prevent support
4. Assigns Yes/No with reasoning

### 5. Output Format

The tool generates a markdown report with:

#### A. Coverage Summary Table
```markdown
| Framework | Supported | Total | Coverage % |
|-----------|-----------|-------|------------|
| Robot Framework | 4 | 5 | 80.0% |
| Selenium | 3 | 5 | 60.0% |
| k6 | 1 | 5 | 20.0% |
```

#### B. Detailed Coverage Matrix
```markdown
| Test Case | Description | Capability | Robot Framework | Selenium | k6 |
|-----------|-------------|------------|-----------------|----------|-----|
| TC001 | Verify user login | UI Automation | ✅ Yes | ✅ Yes | ❌ No |
| TC002 | Verify dashboard | UI Validation | ✅ Yes | ✅ Yes | ❌ No |
| TC003 | Verify API response | API Performance | ✅ Yes | ❌ No | ✅ Yes |
| TC004 | Verify file download | File Handling | ✅ Yes | ✅ Yes | ❌ No |
| TC005 | Proprietary message | Custom Library | ❌ No | ❌ No | ❌ No |
```

#### C. Individual Test Case Analysis
```markdown
### TC001: Verify user login
**Required Capability:** UI Automation

**Supported by:**
- ✅ **Robot Framework**: Web UI testing capable
- ✅ **Selenium**: Supports browser testing

**Not supported by:**
- ❌ **k6**: No UI Automation capability
```

#### D. Recommendation
```markdown
## Recommendation

**Best Framework: Robot Framework**
- Covers 4 out of 5 test cases (80.0%)
- Languages: Python, Robot DSL
- License: Apache 2.0

⚠️ **Coverage Gaps:** 1 test case(s) not supported

Consider:
- **Custom extension** for: TC005 (proprietary library support)
```

---

## Usage Examples

### Example 1: Simple Coverage Analysis

**User Input:**
```
I have 3 test cases:
1. TC001: User login UI test
2. TC002: API response validation  
3. TC003: Load testing

Analyze coverage for Playwright, Selenium, and k6
```

**What Happens:**
1. LLM parses the test cases and capabilities
2. LLM calls `analyze_test_case_coverage` tool
3. Tool generates coverage matrix
4. LLM presents results in chat

### Example 2: Using Sample File

Upload the sample file `data/samples/sample_test_case_analysis.json` and say:

```
Analyze the test case coverage from the uploaded JSON file
```

### Example 3: Migration Decision

**User Input:**
```
I'm migrating from Selenium. I have these test types:
- 50 UI tests
- 20 API tests
- 10 file download tests
- 5 performance tests

Which framework gives best coverage?
```

**LLM Response:**
- Converts to test case format
- Calls coverage tool
- Shows detailed matrix
- Recommends best framework with reasoning

---

## How to Use in Chat

### Method 1: Direct Question
Simply ask in natural language:

```
"Analyze test case coverage for Robot Framework vs Selenium vs Cypress"
```

Include your test cases in the message:
```
I have these test cases:
- Login page UI testing
- REST API validation
- Performance testing

Which framework covers all these?
```

### Method 2: Upload JSON File
1. Upload a JSON file with test cases (see sample format)
2. Ask: "Analyze the test case coverage from my file"

### Method 3: Conversational
```
User: "I need to test UI, API, and performance"
Assistant: "Let me analyze coverage..."
[LLM calls the tool automatically]
```

---

## Supported Capabilities

The tool recognizes these high-level capabilities:

1. **UI Automation** - Browser-based testing, web apps
2. **UI Validation** - Visual regression, accessibility
3. **API Testing** - REST, GraphQL, microservices
4. **API Performance** - Response time, throughput
5. **Performance Testing** - Load testing, stress testing
6. **Load Testing** - Concurrent users, scalability
7. **File Handling** - Upload, download, validation
8. **Mobile Testing** - iOS, Android apps
9. **Database Testing** - SQL queries, data validation
10. **Custom Library** - Extensibility, plugins

---

## Technical Implementation

### Architecture

```
User Query
    ↓
LLM parses test cases
    ↓
Calls analyze_test_case_coverage tool
    ↓
ToolExecutor._analyze_test_case_coverage()
    ↓
For each test case:
    - Map capability to framework features
    - Check each framework in knowledge base
    - Verify capabilities, architecture fit, limitations
    - Record Yes/No with reasoning
    ↓
Calculate coverage percentages
    ↓
Generate markdown report:
    - Summary table
    - Detailed matrix
    - Individual analysis
    - Recommendation
    ↓
Return to LLM
    ↓
LLM presents results in chat
```

### Code Location

**Tool registration:**
- File: `src/tools/executor.py`
- Method: `_analyze_test_case_coverage()`

**Coverage engine:**
- File: `src/tools/coverage_engine.py`
- Functions: `analyze_coverage()`, `render_coverage_report()`

**Sample Data:**
- File: `data/samples/sample_test_case_analysis.json`

---

## Advanced Features

### 1. Framework Auto-Detection
If frameworks are not specified, the tool evaluates ALL frameworks in the knowledge base (17+).

### 2. Capability Inference
The tool infers framework capabilities from:
- Explicit capability flags
- Architecture fit (web_spa, api_only, etc.)
- Framework category (testing, performance, IaC)

### 3. Limitation Checking
The tool checks framework limitations to ensure accurate coverage:
- "Cannot test..." → blocks capability
- "No XYZ support" → blocks capability

### 4. Gap Analysis
Identifies test cases not covered by top framework and suggests complementary frameworks.

### 5. Detailed Reasoning
Every Yes/No decision includes:
- Feature that enables support
- OR reason for lack of support

---

## Example Output

Here's what you'll see for the sample test cases:

### Coverage Summary
```
Robot Framework: 80% (4/5)
Selenium: 60% (3/5)
k6: 20% (1/5)
```

### Key Insights
- **Robot Framework** best for comprehensive coverage
- **k6** only for API performance
- **Selenium** lacks performance testing
- **None** support TC005 (custom library) - needs extension

### Recommendation
**Choose Robot Framework** + custom extension for proprietary libraries

---

## Tips for Best Results

### 1. Be Specific with Capabilities
✅ Good: "UI Automation", "API Performance Testing"
❌ Vague: "Testing", "Automation"

### 2. Include All Test Types
Don't forget:
- Special UI (Shadow DOM, iframes)
- Non-functional (performance, security)
- Integration (database, messaging)

### 3. Specify Target Frameworks
Narrow down to 3-5 relevant frameworks for focused comparison.

### 4. Use Consistent Terminology
The tool understands variations:
- "UI Testing" = "UI Automation" = "Web Testing"
- "API Testing" = "REST API" = "API Validation"

---

## Troubleshooting

### Issue: Tool Not Called
**Solution**: Be explicit: "Analyze test case coverage for..."

### Issue: Unexpected Results
**Solution**: Check capability names match supported list above

### Issue: Missing Framework
**Solution**: Framework must exist in `data/frameworks/*.yaml`

### Issue: Wrong Coverage
**Solution**: Review framework YAML file for correct capabilities

---

## Integration with Existing Features

This tool works seamlessly with:

1. **Uploaded Test Files** - Auto-extracts test cases from code
2. **Case Studies** - Parses test scenarios from documents
3. **Framework Comparison** - Complements scoring engine
4. **Migration Planning** - Informs migration path decisions

---

## Future Enhancements

Planned improvements:
- [ ] Parse test cases from uploaded code files
- [ ] Extract capabilities from test names/comments
- [ ] Support test case priorities/weights
- [ ] Estimate migration effort per test case
- [ ] Generate sample code for framework-specific tests

---

## Related Documentation

- **High-Level Design**: `docs/HLD.md`
- **Tool Executor**: `src/tools/executor.py` (all 11 tools)
- **Coverage Engine**: `src/tools/coverage_engine.py`
- **Framework Data**: `data/frameworks/*.yaml`
- **Sample Input**: `data/samples/sample_test_case_analysis.json`

---

**Status**: ✅ Implemented and ready to use!

Try it now: "Analyze test case coverage for my test suite"
