<#
.SYNOPSIS
    Copy a migrated Robot Framework suite (default: the verifyHAsettings-robot
    project) into an existing Kubernetes pod, install its dependencies, set the
    required environment values, and optionally run the suite.

.DESCRIPTION
    Injects the suite into an ALREADY-RUNNING pod without rebuilding an image.
    The suite's own Dockerfile + k8s/job.yaml cover the baked-image path.

    The "required values" it sets inside the container mirror the suite's
    config/env.sample:
      HA_ENABLED   must be 'true' or the suite skips (k6 __ENV.HA_ENABLED gate)
      ATS_ENABLED  ATS is only checked when 'true'
      NAMESPACE    target namespace (only set when -SuiteNamespace is given;
                   in-cluster it is auto-detected from the service account)

.PARAMETER Pod
    Pod name (required).

.PARAMETER Namespace
    Kubernetes namespace the pod lives in (optional; defaults to current context).

.PARAMETER SuiteRoot
    Path to the migrated suite project. Defaults to the sibling
    verifyHAsettings-robot folder next to automation-framework-advisor.

.PARAMETER Container
    Container name inside the pod (optional; only needed for multi-container pods).

.PARAMETER Dest
    Destination directory inside the container. Default: /suite

.PARAMETER HaEnabled
    Value for HA_ENABLED. Default: true

.PARAMETER AtsEnabled
    Value for ATS_ENABLED. Default: false

.PARAMETER SuiteNamespace
    Value for the in-container NAMESPACE env var (local/kubeconfig runs).

.PARAMETER Suite
    Robot suite file to run. Default: tests/verify_ha_settings.robot

.PARAMETER Run
    Run the suite after copying + installing.

.EXAMPLE
    .\scripts\deploy_ha_suite_to_pod.ps1 -Pod ha-runner-0 -Namespace esoa-example -Run

.EXAMPLE
    .\scripts\deploy_ha_suite_to_pod.ps1 -Pod ha-runner-0 -Namespace esoa-example `
        -AtsEnabled true -Suite tests/verify_ha_settings_per_service.robot -Run
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Pod,
    [string]$Namespace,
    [string]$SuiteRoot,
    [string]$Container,
    [string]$Dest = '/suite',
    [string]$HaEnabled = 'true',
    [string]$AtsEnabled = 'false',
    [string]$SuiteNamespace,
    [string]$Suite = 'tests/verify_ha_settings.robot',
    [switch]$Run
)

$ErrorActionPreference = 'Stop'

# Parts of the suite to copy (relative to the suite root).
$Parts = @('libraries', 'variables', 'resources', 'tests', 'requirements.txt')

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = (Resolve-Path (Join-Path $ScriptDir '..')).Path

if (-not $SuiteRoot) {
    $SuiteRoot = Join-Path $RepoDir '..\verifyHAsettings-robot'
}

function Info([string]$msg) { Write-Host ">> $msg" }
function Die([string]$msg) { Write-Error $msg; exit 1 }

function Require-Command([string]$name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Die "$name not found on PATH"
    }
}

# Build the env-export prefix the in-container shell will evaluate.
function Build-EnvExports {
    $exports = "export HA_ENABLED='$HaEnabled' ATS_ENABLED='$AtsEnabled'"
    if ($SuiteNamespace) { $exports += " NAMESPACE='$SuiteNamespace'" }
    return $exports
}

function Invoke-External {
    param([string]$Exe, [string[]]$Args)
    & $Exe @Args
    if ($LASTEXITCODE -ne 0) { Die "$Exe $($Args -join ' ') failed (exit $LASTEXITCODE)" }
}

Require-Command kubectl

if (-not (Test-Path $SuiteRoot)) { Die "Suite root not found: $SuiteRoot (pass -SuiteRoot)" }
$SuiteRoot = (Resolve-Path $SuiteRoot).Path

$nsFlag = @(); if ($Namespace) { $nsFlag = @('-n', $Namespace) }
$cFlag = @();  if ($Container) { $cFlag = @('-c', $Container) }

Info "Source (suite root on this machine): $SuiteRoot"
Info "Destination (inside the pod):       ${Pod}:$Dest  (namespace: $(if ($Namespace) { $Namespace } else { '<current-context>' }))"
Info "Will copy: $($Parts -join ', ')  ->  $Dest/<name>"

Info "Ensuring destination $Dest exists in the pod"
Invoke-External kubectl ($nsFlag + @('exec', $Pod) + $cFlag + @('--', 'sh', '-c', "mkdir -p '$Dest'"))

foreach ($part in $Parts) {
    $src = Join-Path $SuiteRoot $part
    if (-not (Test-Path $src)) { Die "Missing suite part: $part under $SuiteRoot" }
    Info "Copying $part -> ${Pod}:$Dest/"
    Invoke-External kubectl ($nsFlag + @('cp', $src, "${Pod}:$Dest/$part") + $cFlag)
}

Info 'Installing Python dependencies in the pod'
$pipCmd = "cd '$Dest' && (pip install --no-cache-dir -r requirements.txt || python -m pip install --no-cache-dir -r requirements.txt)"
Invoke-External kubectl ($nsFlag + @('exec', $Pod) + $cFlag + @('--', 'sh', '-c', $pipCmd))

$runCmd = "cd '$Dest' && $(Build-EnvExports) && robot --outputdir results '$Suite'"
if ($Run) {
    Info "Running suite: $Suite"
    Invoke-External kubectl ($nsFlag + @('exec', $Pod) + $cFlag + @('--', 'sh', '-c', $runCmd))
}
else {
    Info 'Suite copied. To run it:'
    Write-Host "  kubectl $($nsFlag -join ' ') exec $Pod $($cFlag -join ' ') -- sh -c `"$runCmd`""
}

Info 'Done.'
