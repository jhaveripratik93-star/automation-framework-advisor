*** Settings ***
# Robot Framework — UI E2E test example
# Install: pip install robotframework robotframework-seleniumlibrary
# Run:     robot robot_example.robot

Library    SeleniumLibrary

*** Variables ***
${URL}         https://example.com/login
${BROWSER}     chrome

*** Test Cases ***
Login With Valid Credentials
    Open Browser    ${URL}    ${BROWSER}
    Input Text      [data-testid='username']    admin
    Input Password  [data-testid='password']    password123
    Click Button    [data-testid='login-btn']
    Location Should Contain    /dashboard
    Element Text Should Be     h1    Dashboard
    [Teardown]    Close Browser
