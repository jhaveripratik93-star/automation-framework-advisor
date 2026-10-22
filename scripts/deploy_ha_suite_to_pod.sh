#!/usr/bin/env bash
#
# deploy_ha_suite_to_pod.sh
# -------------------------
# Copy a migrated test suite into an existing Kubernetes pod. COPY ONLY:
# it does not install dependencies or run anything.
#
# By default it auto-discovers and copies every top-level entry in the suite
# root, so it works for Robot suites (libraries/variables/resources/tests) and
# Playwright/pytest suites (tests/helpers/conftest.py/pytest.ini) alike.
# Restrict what gets copied with --parts.
#
# This injects the suite into an ALREADY-RUNNING pod without rebuilding an
# image. The suite's own Dockerfile + k8s/job.yaml cover the baked-image path.
#
# Usage:
#   scripts/deploy_ha_suite_to_pod.sh --pod <pod> [--namespace <ns>] \
#       [--suite-root <path>] [--container <name>] \
#       [--parts a,b,c] [--dest /suite]
#
# Examples:
#   scripts/deploy_ha_suite_to_pod.sh --pod robot-7974df9dc7-5s5rb \
#       --namespace robot-poc --suite-root ../playwright_combined
#
#   scripts/deploy_ha_suite_to_pod.sh --pod ha-runner-0 --namespace esoa-example \
#       --suite-root ../verifyHAsettings-robot --parts tests,resources,requirements.txt
#
set -euo pipefail

# Git Bash / MSYS2 on Windows rewrites arguments that look like Unix paths
# (e.g. the in-container "/suite" dest, or the "sh -c ..." remote commands)
# into Windows paths before they reach kubectl. Scope the exclusion to ONLY
# those in-container tokens so that normal path handling still applies to
# everything else -- in particular KUBECONFIG, which a Windows kubectl.exe
# must receive as a Windows path, not a /c/Users/... MSYS path.
export MSYS2_ARG_CONV_EXCL="/suite*:sh:-c:mkdir*:cd*"

# Normalize KUBECONFIG for a Windows kubectl.exe. If it holds an MSYS/POSIX
# path (e.g. /c/Users/... or ~/.kube/..), convert it to a Windows path with
# cygpath so kubectl can actually find the file. Harmless on WSL/Linux where
# cygpath is absent (we just leave the value as-is).
if [[ -n "${KUBECONFIG:-}" ]] && command -v cygpath >/dev/null 2>&1; then
  KUBECONFIG="$(cygpath -w "$KUBECONFIG" 2>/dev/null || echo "$KUBECONFIG")"
  export KUBECONFIG
fi

# ---- defaults --------------------------------------------------------------
POD=""
K8S_NAMESPACE=""
CONTAINER=""
DEST="/suite"

# Where the migrated suite lives. Defaults to the sibling verifyHAsettings-robot
# project relative to this repo (automation-framework-advisor/..).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SUITE_ROOT="${REPO_DIR}/../verifyHAsettings-robot"

# Suite content to copy (relative to SUITE_ROOT). Empty here means
# "auto-discover": copy every top-level entry found in SUITE_ROOT. This keeps
# the script content-agnostic so it works for Robot suites
# (libraries/variables/resources/tests) and Playwright/pytest suites
# (tests/helpers/conftest.py/pytest.ini) alike. Override with --parts.
PARTS=()

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo ">> $*"; }

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

# ---- parse args ------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pod)             POD="$2"; shift 2 ;;
    --namespace)       K8S_NAMESPACE="$2"; shift 2 ;;
    --suite-root)      SUITE_ROOT="$2"; shift 2 ;;
    --parts)           IFS=',' read -r -a PARTS <<< "$2"; shift 2 ;;
    --container)       CONTAINER="$2"; shift 2 ;;
    --dest)            DEST="$2"; shift 2 ;;
    -h|--help)         usage 0 ;;
    *) die "Unknown argument: $1 (use --help)" ;;
  esac
done

command -v kubectl >/dev/null 2>&1 || die "kubectl not found on PATH"
[[ -n "$POD" ]] || die "--pod is required"
[[ -d "$SUITE_ROOT" ]] || die "Suite root not found: ${SUITE_ROOT} (pass --suite-root)"
SUITE_ROOT="$(cd "$SUITE_ROOT" && pwd)"

# If no explicit --parts were given, auto-discover every top-level entry in the
# suite root (files and directories, including dotfiles like conftest.py's
# siblings) so the script copies whatever the suite actually contains.
if [[ ${#PARTS[@]} -eq 0 ]]; then
  while IFS= read -r entry; do
    PARTS+=("$(basename "$entry")")
  done < <(find "$SUITE_ROOT" -mindepth 1 -maxdepth 1 \
             ! -name '.' ! -name '..' ! -name '.git' ! -name '__pycache__' \
             ! -name '.pytest_cache' ! -name 'results' | sort)
  [[ ${#PARTS[@]} -gt 0 ]] || die "Suite root is empty: ${SUITE_ROOT}"
fi

# ---- deploy ----------------------------------------------------------------
ns_flag=()
[[ -n "$K8S_NAMESPACE" ]] && ns_flag=(-n "$K8S_NAMESPACE")
c_flag=()
[[ -n "$CONTAINER" ]] && c_flag=(-c "$CONTAINER")

info "Source (suite root on this machine): ${SUITE_ROOT}"
info "Destination (inside the pod):       ${POD}:${DEST}  (namespace: ${K8S_NAMESPACE:-<current-context>})"
info "Will copy: ${PARTS[*]}  ->  ${DEST}/<name>"

info "Ensuring destination ${DEST} exists in the pod"
kubectl "${ns_flag[@]}" exec "$POD" "${c_flag[@]}" -- sh -c "mkdir -p '${DEST}'"

# `kubectl cp` splits src/dest on the FIRST colon to decide which side is
# remote. On Windows an absolute local path like C:\Users\... contains a
# drive-letter colon, so kubectl sees colons on both sides and errors with
#   "one of src or dest must be a local file specification".
# Avoid it entirely by running from inside the suite root and passing each
# source as a bare RELATIVE name (no drive letter, no colon). kubectl resolves
# the local source against the current directory.
cd "$SUITE_ROOT"

for part in "${PARTS[@]}"; do
  [[ -e "${part}" ]] || die "Missing suite part: ${part} under ${SUITE_ROOT}"
  info "Copying ${part} -> ${POD}:${DEST}/"
  # kubectl cp copies the source into DEST, preserving the basename.
  kubectl "${ns_flag[@]}" cp "${part}" "${POD}:${DEST}/${part}" "${c_flag[@]}"
done

info "Copy complete. Suite contents are under ${POD}:${DEST}/"
info "Verify with:"
echo "  kubectl ${ns_flag[*]} exec ${POD} ${c_flag[*]} -- ls -R ${DEST}"

info "Done."
