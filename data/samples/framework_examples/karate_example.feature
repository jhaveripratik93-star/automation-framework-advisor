# Karate — API + UI test example
# Install: Add karate-core to pom.xml (Maven) or build.gradle
# Run:     mvn test  or  gradle test

Feature: Login API

  Background:
    * url 'https://api.example.com'

  Scenario: Login with valid credentials returns token
    Given path '/auth/login'
    And request { username: 'admin', password: 'password123' }
    When method POST
    Then status 200
    And match response.token != null
    And match response.user.role == 'admin'
