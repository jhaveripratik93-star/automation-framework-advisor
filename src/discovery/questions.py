"""Discovery question bank organized by decision vector."""

from dataclasses import dataclass, field


@dataclass
class Question:
    """A single discovery question."""

    id: str
    vector: str  # Decision vector category
    text: str
    follow_ups: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    maps_to: str = ""  # UserProfile field this maps to


DISCOVERY_QUESTIONS = [
    # Vector 1: Application Architecture
    Question(
        id="arch_type",
        vector="application_architecture",
        text="What type of application are you testing?",
        options=[
            "Web SPA (React, Angular, Vue)",
            "Web MPA (traditional server-rendered)",
            "Native Mobile (iOS/Android)",
            "Hybrid Mobile (React Native, Flutter)",
            "Desktop Application",
            "API/Microservices only",
            "Multiple of the above",
        ],
        maps_to="architecture_types",
    ),
    Question(
        id="arch_frontend",
        vector="application_architecture",
        text="What frontend framework or technology is used?",
        follow_ups=["Does it use Shadow DOM components?"],
        maps_to="special_ui",
    ),
    Question(
        id="arch_special_ui",
        vector="application_architecture",
        text="Does your application use any of these special UI elements?",
        options=[
            "Shadow DOM / Web Components",
            "Cross-origin iFrames",
            "Canvas / WebGL",
            "Multi-tab workflows",
            "Multi-domain authentication",
            "None of the above",
        ],
        maps_to="special_ui",
    ),

    # Vector 2: Team Skillset & Language
    Question(
        id="team_language",
        vector="team_skillset",
        text="What is your team's primary programming language?",
        options=["Python", "JavaScript", "TypeScript", "Java", "C#", "Other"],
        maps_to="primary_language",
    ),
    Question(
        id="team_secondary",
        vector="team_skillset",
        text="Any secondary languages the team is comfortable with?",
        maps_to="secondary_languages",
    ),
    Question(
        id="team_experience",
        vector="team_skillset",
        text="What is the team's automation testing experience level?",
        options=["Beginner", "Intermediate", "Advanced", "Expert"],
        maps_to="automation_experience",
    ),
    Question(
        id="team_size",
        vector="team_skillset",
        text="How many people will work on the automation framework?",
        maps_to="team_size",
    ),
    Question(
        id="team_current",
        vector="team_skillset",
        text="What automation framework (if any) are you currently using?",
        follow_ups=[
            "What are the main pain points with it?",
            "How many existing test scripts do you have?",
        ],
        maps_to="current_framework",
    ),

    # Vector 3: Execution Environment
    Question(
        id="env_cicd",
        vector="execution_environment",
        text="What CI/CD tool does your team use?",
        options=[
            "Jenkins",
            "GitHub Actions",
            "GitLab CI",
            "Azure DevOps",
            "CircleCI",
            "Other",
        ],
        maps_to="ci_cd_tool",
    ),
    Question(
        id="env_docker",
        vector="execution_environment",
        text="Do you run tests in Docker containers?",
        options=["Yes", "No", "Planning to"],
        maps_to="containerized",
    ),
    Question(
        id="env_parallel",
        vector="execution_environment",
        text="Is parallel test execution a requirement?",
        options=["Yes, critical", "Nice to have", "Not needed"],
        maps_to="parallel_required",
    ),
    Question(
        id="env_browsers",
        vector="execution_environment",
        text="Which browsers must be supported?",
        options=[
            "Chrome only",
            "Chrome + Firefox",
            "Chrome + Firefox + Safari/WebKit",
            "All major browsers + Edge",
        ],
        maps_to="browsers_required",
    ),
    Question(
        id="env_cloud",
        vector="execution_environment",
        text="Do you use or plan to use a cloud testing grid?",
        options=[
            "BrowserStack",
            "Sauce Labs",
            "LambdaTest",
            "No (on-premise only)",
            "Evaluating options",
        ],
        maps_to="cloud_grid",
    ),

    # Vector 4: Special Requirements
    Question(
        id="req_must_have",
        vector="special_requirements",
        text="What are your MUST-HAVE capabilities? (Select all that apply)",
        options=[
            "Cross-browser testing",
            "API testing",
            "Parallel execution",
            "Docker containerization",
            "Visual regression testing",
            "Mobile web testing",
            "Accessibility testing",
            "Performance/load testing",
        ],
        maps_to="must_support",
    ),

    # Vector 5: Maintenance & Budget
    Question(
        id="budget_preference",
        vector="maintenance_budget",
        text="What is your budget preference for the automation framework?",
        options=[
            "Strictly open-source (no paid tools)",
            "Open-source preferred, small budget for cloud services",
            "Flexible - best tool wins regardless of cost",
            "Commercial tools acceptable if ROI is clear",
        ],
        maps_to="budget",
    ),
    Question(
        id="budget_timeline",
        vector="maintenance_budget",
        text="What is your timeline for the migration/setup? (in weeks)",
        maps_to="timeline_weeks",
    ),
    Question(
        id="legacy_count",
        vector="maintenance_budget",
        text="How many existing test scripts need to be migrated?",
        maps_to="legacy_test_count",
    ),
]
