// REST Assured (Java) — API test example
// Install: Add rest-assured + junit5 to pom.xml
// Run:     mvn test

import io.restassured.RestAssured;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.*;
import static org.hamcrest.Matchers.*;

public class LoginApiTest {

    @Test
    public void loginWithValidCredentialsReturnsToken() {
        RestAssured.baseURI = "https://api.example.com";

        given()
            .contentType("application/json")
            .body("{ \"username\": \"admin\", \"password\": \"password123\" }")
        .when()
            .post("/auth/login")
        .then()
            .statusCode(200)
            .body("token", notNullValue())
            .body("user.role", equalTo("admin"));
    }
}
