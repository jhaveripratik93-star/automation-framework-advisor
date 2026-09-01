// K6 — Performance / Load test example
// Install: https://k6.io/docs/get-started/installation/
// Run:     k6 run k6_example.js

import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 10,          // 10 virtual users
  duration: "30s",  // run for 30 seconds
  thresholds: {
    http_req_duration: ["p(95)<500"],  // 95% of requests under 500ms
    http_req_failed: ["rate<0.01"],    // error rate under 1%
  },
};

export default function () {
  const res = http.post(
    "https://api.example.com/auth/login",
    JSON.stringify({ username: "admin", password: "password123" }),
    { headers: { "Content-Type": "application/json" } }
  );

  check(res, {
    "status is 200": (r) => r.status === 200,
    "token present": (r) => JSON.parse(r.body).token !== undefined,
  });

  sleep(1);
}
