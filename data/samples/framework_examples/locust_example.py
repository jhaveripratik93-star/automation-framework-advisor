# Locust — Performance / Load test example
# Install: pip install locust
# Run:     locust -f locust_example.py --host=https://api.example.com

from locust import HttpUser, task, between


class LoginUser(HttpUser):
    wait_time = between(1, 3)  # wait 1–3s between tasks

    @task
    def login(self):
        response = self.client.post(
            "/auth/login",
            json={"username": "admin", "password": "password123"},
        )
        assert response.status_code == 200
        assert "token" in response.json()
