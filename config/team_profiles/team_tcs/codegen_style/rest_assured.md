## Framework & Imports

REST Assured Java | JUnit/TestNG | fluent API

## Pattern

given().header().body().when().post('/endpoint').then().statusCode(200).body('key', equalTo('val'))

## Imports

import static io.restassured.RestAssured.*; import static org.hamcrest.Matchers.*;

## Structure

public class ApiTest { @BeforeClass public static void setup() { baseURI = '...'; } @Test public void test() {} }
