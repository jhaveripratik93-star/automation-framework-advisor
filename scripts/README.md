# scripts

Utility scripts for the automation-framework-advisor.

## deploy_ha_suite_to_pod

Copies a migrated Robot Framework suite (default: the sibling
`verifyHAsettings-robot` project) into an existing Kubernetes pod, installs its
dependencies, sets the required environment values, and optionally runs it.

Use this to inject the suite into an **already-running** pod without rebuilding
an image. (The suite's own `Dockerfile` + `k8s/job.yaml` cover the baked-image
path.)

Two equivalent entry points — pick whichever shell you're in:

| Shell                         | Script                        |
|-------------------------------|-------------------------------|
| Git Bash / WSL / Linux / CI   | `deploy_ha_suite_to_pod.sh`   |
| PowerShell                    | `deploy_ha_suite_to_pod.ps1`  |

### Git Bash / Linux / CI

```bash
./scripts/deploy_ha_suite_to_pod.sh --pod ha-runner-0 --namespace esoa-example --run
```

The script sets `MSYS_NO_PATHCONV=1` internally so Git Bash on Windows does not
rewrite the in-container `/suite` path into a Windows path.

### PowerShell

```powershell
.\scripts\deploy_ha_suite_to_pod.ps1 -Pod ha-runner-0 -Namespace esoa-example -Run
```

### What it sets (the "required values")

These mirror the suite's `config/env.sample`:

| Env var       | Flag (sh / ps1)                          | Default |
|---------------|------------------------------------------|---------|
| `HA_ENABLED`  | `--ha-enabled` / `-HaEnabled`            | `true`  |
| `ATS_ENABLED` | `--ats-enabled` / `-AtsEnabled`          | `false` |
| `NAMESPACE`   | `--suite-namespace` / `-SuiteNamespace`  | unset (auto-detected in-cluster) |

### Common flags

| Purpose                                  | sh                        | ps1               |
|------------------------------------------|---------------------------|-------------------|
| Pod name (required)                      | `--pod`                   | `-Pod`            |
| Pod namespace                            | `--namespace`             | `-Namespace`      |
| Path to the migrated suite               | `--suite-root`            | `-SuiteRoot`      |
| Container (multi-container pods)         | `--container`             | `-Container`      |
| Copy destination inside the pod          | `--dest` (default /suite) | `-Dest`           |
| Suite file to run                        | `--suite`                 | `-Suite`          |
| Run after copying                        | `--run`                   | `-Run`            |

Omit `--run` / `-Run` to copy + install only; the script then prints the exact
`kubectl exec` command to run it yourself.

### Prerequisites

- `kubectl` on PATH, pointed at the target cluster.
- The target pod must have a shell, `pip`, and `robot` (or `python -m pip`)
  available, plus read access (RBAC) to deployments, statefulsets, pods and
  services in the namespace — see the suite's `k8s/rbac.yaml`.
