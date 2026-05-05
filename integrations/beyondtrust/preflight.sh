#!/usr/bin/env bash

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_SCRIPT="${SCRIPT_DIR}/beyondtrust.py"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"
ENV_FILE="${SCRIPT_DIR}/.env"
VENV_PYTHON="${SCRIPT_DIR}/venv/bin/python3"
LOG_FILE="${SCRIPT_DIR}/preflight_$(date +%Y%m%d_%H%M%S).log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'

TESTS_PASSED=0
TESTS_FAILED=0
TESTS_WARNING=0

tee_log() { tee -a "$LOG_FILE"; }
print_header() { echo -e "\n${BOLD}$1${NC}" | tee_log; }
print_success() { echo -e "${GREEN}✓${NC} $1" | tee_log; ((TESTS_PASSED++)); }
print_fail() { echo -e "${RED}✗${NC} $1" | tee_log; ((TESTS_FAILED++)); }
print_warning() { echo -e "${YELLOW}⚠${NC} $1" | tee_log; ((TESTS_WARNING++)); }
print_info() { echo -e "${BLUE}ℹ${NC} $1" | tee_log; }

mask_value() {
  local value="$1"
  if [[ -z "$value" ]]; then
    echo ""
  else
    echo "${value:0:8}..."
  fi
}

check_env_var() {
  local var_name=$1
  local var_value=$2
  local optional=${3:-required}

  if [[ -z "$var_value" ]]; then
    if [[ "$optional" == "optional" ]]; then
      print_info "$var_name not set (optional)"
    else
      print_fail "$var_name is not set"
    fi
  elif [[ "$var_value" =~ ^your_.* ]] || [[ "$var_value" =~ https://your-.* ]]; then
    print_warning "$var_name contains placeholder value"
  else
    if [[ "$var_name" =~ PASSWORD|KEY|TOKEN|SECRET ]]; then
      print_success "$var_name set ($(mask_value "$var_value"))"
    else
      print_success "$var_name set"
    fi
  fi
}

load_env_if_present() {
  if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
  fi
}

check_system_requirements() {
  print_header "1) System Requirements"

  if command -v python3 >/dev/null 2>&1; then
    local pyver
    pyver="$(python3 - <<'PYEOF'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PYEOF
)"
    local major minor
    major="${pyver%%.*}"
    minor="$(echo "$pyver" | cut -d. -f2)"
    if [[ "$major" -gt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -ge 9 ]]; }; then
      print_success "Python version is ${pyver} (>= 3.9)"
    else
      print_fail "Python version is ${pyver}; require >= 3.9"
    fi
  else
    print_fail "python3 not found"
  fi

  if command -v pip3 >/dev/null 2>&1; then
    print_success "pip3 found"
  else
    print_fail "pip3 not found"
  fi

  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    print_success "Running inside virtual environment (${VIRTUAL_ENV})"
  else
    print_warning "Not running inside a virtual environment"
  fi

  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    print_info "OS detected: ${PRETTY_NAME:-unknown}"
  elif [[ "$(uname -s)" == "Darwin" ]]; then
    print_info "OS detected: macOS"
  else
    print_warning "Unable to identify OS"
  fi

  if command -v curl >/dev/null 2>&1; then
    print_success "curl found"
  else
    print_fail "curl not found"
  fi

  if command -v jq >/dev/null 2>&1; then
    print_success "jq found"
  else
    print_warning "jq not found (optional)"
  fi
}

normalize_pkg_name_to_import() {
  case "$1" in
    python-dotenv) echo "dotenv" ;;
    oaaclient) echo "oaaclient" ;;
    requests) echo "requests" ;;
    urllib3) echo "urllib3" ;;
    *) echo "$1" ;;
  esac
}

check_python_dependencies() {
  print_header "2) Python Dependencies"

  if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    print_fail "requirements.txt not found at ${REQUIREMENTS_FILE}"
    return
  fi

  local py_exec="python3"
  if [[ -x "$VENV_PYTHON" ]]; then
    py_exec="$VENV_PYTHON"
    print_info "Using venv python: ${py_exec}"
  else
    print_warning "venv python not found; using system python3"
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(echo "$line" | xargs)"
    [[ -z "$line" ]] && continue

    local pkg import_name
    pkg="$(echo "$line" | sed -E 's/[<>=!~].*$//')"
    import_name="$(normalize_pkg_name_to_import "$pkg")"

    if "$py_exec" - <<PYEOF >/dev/null 2>&1
import importlib
m = importlib.import_module("${import_name}")
print(getattr(m, "__version__", "unknown"))
PYEOF
    then
      local ver
      ver="$($py_exec - <<PYEOF
import importlib
m = importlib.import_module("${import_name}")
print(getattr(m, "__version__", "unknown"))
PYEOF
)"
      print_success "${pkg} importable (version ${ver})"
    else
      print_fail "${pkg} is not importable"
    fi
  done < "$REQUIREMENTS_FILE"

  if [[ $TESTS_FAILED -gt 0 ]]; then
    print_info "Install deps with: ${SCRIPT_DIR}/venv/bin/pip install -r ${REQUIREMENTS_FILE}"
  fi
}

check_configuration_file() {
  print_header "3) Configuration File"

  if [[ ! -f "$ENV_FILE" ]]; then
    print_fail ".env not found at ${ENV_FILE}"
    print_info "Use option 10 to generate a template"
    return
  fi

  local perms
  perms="$(stat -c %a "$ENV_FILE" 2>/dev/null || stat -f %Lp "$ENV_FILE" 2>/dev/null || echo "unknown")"
  if [[ "$perms" == "600" ]]; then
    print_success ".env permissions are 600"
  else
    print_warning ".env permissions are ${perms}; recommended: chmod 600 ${ENV_FILE}"
  fi

  load_env_if_present

  check_env_var "VEZA_URL" "${VEZA_URL:-}"
  check_env_var "VEZA_API_KEY" "${VEZA_API_KEY:-}"
  check_env_var "BEYONDTRUST_BASE_URL" "${BEYONDTRUST_BASE_URL:-}"

  check_env_var "BEYONDTRUST_API_TOKEN" "${BEYONDTRUST_API_TOKEN:-}" optional
  check_env_var "BEYONDTRUST_USERNAME" "${BEYONDTRUST_USERNAME:-}" optional
  check_env_var "BEYONDTRUST_PASSWORD" "${BEYONDTRUST_PASSWORD:-}" optional

  if [[ -z "${BEYONDTRUST_API_TOKEN:-}" ]]; then
    if [[ -n "${BEYONDTRUST_USERNAME:-}" && -n "${BEYONDTRUST_PASSWORD:-}" ]]; then
      print_success "BeyondTrust auth fallback (username/password) configured"
    else
      print_fail "Set BEYONDTRUST_API_TOKEN or both BEYONDTRUST_USERNAME and BEYONDTRUST_PASSWORD"
    fi
  fi

  check_env_var "BEYONDTRUST_AUTH_ENDPOINT" "${BEYONDTRUST_AUTH_ENDPOINT:-}" optional
  check_env_var "BEYONDTRUST_MANAGED_ACCOUNTS_ENDPOINT" "${BEYONDTRUST_MANAGED_ACCOUNTS_ENDPOINT:-}" optional
  check_env_var "BEYONDTRUST_DEVICES_ENDPOINT" "${BEYONDTRUST_DEVICES_ENDPOINT:-}" optional
  check_env_var "BEYONDTRUST_APPLICATIONS_ENDPOINT" "${BEYONDTRUST_APPLICATIONS_ENDPOINT:-}" optional
  check_env_var "BEYONDTRUST_ACCESS_ASSIGNMENTS_ENDPOINT" "${BEYONDTRUST_ACCESS_ASSIGNMENTS_ENDPOINT:-}" optional
}

parse_host_port_from_url() {
  local url="$1"
  local host port
  host="$(echo "$url" | sed -E 's#^https?://([^/:]+).*$#\1#')"
  port="$(echo "$url" | sed -nE 's#^https?://[^/:]+:([0-9]+).*$#\1#p')"
  if [[ -z "$port" ]]; then
    if [[ "$url" == https://* ]]; then
      port=443
    else
      port=80
    fi
  fi
  echo "${host}|${port}"
}

tcp_check() {
  local host="$1" port="$2"
  if command -v nc >/dev/null 2>&1; then
    nc -zw 5 "$host" "$port" >/dev/null 2>&1
    return $?
  fi

  if (echo > "/dev/tcp/${host}/${port}") >/dev/null 2>&1; then
    return 0
  fi

  curl -m 5 "telnet://${host}:${port}" >/dev/null 2>&1
}

https_check() {
  local url="$1"
  curl -s -o /dev/null -w "%{http_code}|%{time_total}" -m 10 "$url"
}

check_network_connectivity() {
  print_header "4) Network Connectivity"
  load_env_if_present

  if [[ -z "${BEYONDTRUST_BASE_URL:-}" ]]; then
    print_fail "BEYONDTRUST_BASE_URL is not set"
  else
    local hp host port result
    hp="$(parse_host_port_from_url "$BEYONDTRUST_BASE_URL")"
    host="${hp%%|*}"
    port="${hp##*|}"

    if tcp_check "$host" "$port"; then
      print_success "TCP connectivity to BeyondTrust ${host}:${port}"
    else
      print_fail "Cannot reach BeyondTrust ${host}:${port}"
    fi

    result="$(https_check "$BEYONDTRUST_BASE_URL")"
    print_info "BeyondTrust HTTPS check (code|latency): ${result}"
  fi

  if [[ -z "${VEZA_URL:-}" ]]; then
    print_fail "VEZA_URL is not set"
  else
    local veza_url
    if [[ "$VEZA_URL" =~ ^https?:// ]]; then
      veza_url="$VEZA_URL"
    else
      veza_url="https://${VEZA_URL}"
    fi

    local hp2 host2 port2 result2
    hp2="$(parse_host_port_from_url "$veza_url")"
    host2="${hp2%%|*}"
    port2="${hp2##*|}"

    if tcp_check "$host2" "$port2"; then
      print_success "TCP connectivity to Veza ${host2}:${port2}"
    else
      print_fail "Cannot reach Veza ${host2}:${port2}"
    fi

    result2="$(https_check "$veza_url")"
    print_info "Veza HTTPS check (code|latency): ${result2}"
  fi
}

check_api_authentication() {
  print_header "5) API Authentication"
  load_env_if_present

  if [[ -z "${BEYONDTRUST_BASE_URL:-}" ]]; then
    print_fail "Cannot test BeyondTrust auth without BEYONDTRUST_BASE_URL"
  else
    local accounts_ep
    accounts_ep="${BEYONDTRUST_MANAGED_ACCOUNTS_ENDPOINT:-/api/public/v3/ManagedAccounts}"

    print_info "[DEBUG] BeyondTrust target: ${BEYONDTRUST_BASE_URL}${accounts_ep}"

    if [[ -n "${BEYONDTRUST_API_TOKEN:-}" ]]; then
      local code
      code="$(curl -s -o /tmp/bt_auth_test.out -w "%{http_code}" -H "Authorization: Bearer ${BEYONDTRUST_API_TOKEN}" "${BEYONDTRUST_BASE_URL}${accounts_ep}")"
      if [[ "$code" == "200" ]]; then
        print_success "BeyondTrust bearer token authentication succeeded"
      else
        print_fail "BeyondTrust bearer token auth failed (HTTP ${code})"
      fi
    elif [[ -n "${BEYONDTRUST_USERNAME:-}" && -n "${BEYONDTRUST_PASSWORD:-}" ]]; then
      local auth_ep auth_code
      auth_ep="${BEYONDTRUST_AUTH_ENDPOINT:-/api/public/v3/Auth/SignAppin}"
      print_info "[DEBUG] BeyondTrust auth endpoint: ${BEYONDTRUST_BASE_URL}${auth_ep}"
      auth_code="$(curl -s -o /tmp/bt_login_test.out -w "%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"${BEYONDTRUST_USERNAME}\",\"password\":\"${BEYONDTRUST_PASSWORD}\"}" \
        "${BEYONDTRUST_BASE_URL}${auth_ep}")"
      if [[ "$auth_code" == "200" || "$auth_code" == "201" ]]; then
        print_success "BeyondTrust username/password auth endpoint call succeeded"
      else
        print_fail "BeyondTrust username/password auth failed (HTTP ${auth_code})"
      fi
    else
      print_fail "No BeyondTrust credentials available for auth test"
    fi
  fi

  if [[ -z "${VEZA_URL:-}" || -z "${VEZA_API_KEY:-}" ]]; then
    print_fail "Cannot test Veza auth without VEZA_URL and VEZA_API_KEY"
  else
    local veza_url
    if [[ "$VEZA_URL" =~ ^https?:// ]]; then
      veza_url="$VEZA_URL"
    else
      veza_url="https://${VEZA_URL}"
    fi

    local code
    code="$(curl -s -o /tmp/veza_auth_test.out -w "%{http_code}" -H "Authorization: Bearer ${VEZA_API_KEY}" "${veza_url}/api/v1/providers")"
    if [[ "$code" == "200" ]]; then
      print_success "Veza API key authentication succeeded"
    else
      print_fail "Veza API key authentication failed (HTTP ${code})"
      print_info "Partial response: $(head -c 400 /tmp/veza_auth_test.out 2>/dev/null)"
    fi
  fi
}

check_api_endpoint_access() {
  print_header "6) API Endpoint Access"
  load_env_if_present

  if [[ -z "${VEZA_URL:-}" || -z "${VEZA_API_KEY:-}" ]]; then
    print_fail "Cannot test Veza Query endpoint without VEZA_URL and VEZA_API_KEY"
    return
  fi

  local veza_url
  if [[ "$VEZA_URL" =~ ^https?:// ]]; then
    veza_url="$VEZA_URL"
  else
    veza_url="https://${VEZA_URL}"
  fi

  local payload status
  payload='{"query":"nodes{InstanceId first:1}"}'
  status="$(curl -s -o /tmp/veza_query_test.out -w "%{http_code}" \
    -X POST "${veza_url}/api/v1/assessments/query_spec:nodes" \
    -H "Authorization: Bearer ${VEZA_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$payload")"

  if [[ "$status" == "200" ]]; then
    print_success "Veza query endpoint accessible"
  else
    print_fail "Veza query endpoint test failed (HTTP ${status})"
    python3 - <<'PYEOF' 2>/dev/null | tee_log
import json
from pathlib import Path
p = Path('/tmp/veza_query_test.out')
if p.exists():
    try:
        print(json.dumps(json.loads(p.read_text()), indent=2)[:1000])
    except Exception:
        print(p.read_text()[:1000])
PYEOF
  fi

  if [[ -n "${BEYONDTRUST_BASE_URL:-}" ]]; then
    local endpoints=(
      "${BEYONDTRUST_MANAGED_ACCOUNTS_ENDPOINT:-/api/public/v3/ManagedAccounts}"
      "${BEYONDTRUST_DEVICES_ENDPOINT:-/api/public/v3/ManagedSystems}"
      "${BEYONDTRUST_APPLICATIONS_ENDPOINT:-/api/public/v3/Applications}"
      "${BEYONDTRUST_ACCESS_ASSIGNMENTS_ENDPOINT:-/api/public/v3/AccessAssignments}"
    )

    for ep in "${endpoints[@]}"; do
      local code
      if [[ -n "${BEYONDTRUST_API_TOKEN:-}" ]]; then
        code="$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer ${BEYONDTRUST_API_TOKEN}" "${BEYONDTRUST_BASE_URL}${ep}")"
      else
        code="$(curl -s -o /dev/null -w "%{http_code}" "${BEYONDTRUST_BASE_URL}${ep}")"
      fi

      if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then
        print_success "BeyondTrust endpoint reachable: ${ep} (HTTP ${code})"
      else
        print_warning "BeyondTrust endpoint check returned HTTP ${code}: ${ep}"
      fi
    done
  fi
}

check_deployment_structure() {
  print_header "7) Deployment Structure"

  if [[ -r "$MAIN_SCRIPT" ]]; then
    print_success "Main script present and readable: ${MAIN_SCRIPT}"
  else
    print_fail "Main script missing or unreadable: ${MAIN_SCRIPT}"
  fi

  if [[ -f "$REQUIREMENTS_FILE" ]]; then
    print_success "requirements.txt present"
  else
    print_fail "requirements.txt missing"
  fi

  local log_dir="${SCRIPT_DIR}/logs"
  if [[ -d "$log_dir" ]]; then
    if [[ -w "$log_dir" ]]; then
      print_success "logs/ exists and is writable"
    else
      print_fail "logs/ exists but is not writable"
    fi
  else
    print_warning "logs/ does not exist yet (created on first run)"
  fi

  local current_user
  current_user="$(id -un 2>/dev/null || whoami)"
  print_info "Running user: ${current_user}"
  if [[ "$current_user" != "beyondtrust-veza" ]]; then
    print_warning "Recommended service account is beyondtrust-veza"
  fi

  if [[ "$SCRIPT_DIR" == /opt/VEZA/beyondtrust-veza/scripts* ]]; then
    print_success "Installed under recommended path /opt/VEZA/beyondtrust-veza/scripts/"
  else
    print_info "Current path is ${SCRIPT_DIR}; recommended path is /opt/VEZA/beyondtrust-veza/scripts/"
  fi
}

display_current_configuration() {
  print_header "9) Current Configuration"
  load_env_if_present

  echo "ENV_FILE=${ENV_FILE}" | tee_log
  echo "VEZA_URL=${VEZA_URL:-}" | tee_log
  echo "VEZA_API_KEY=$(mask_value "${VEZA_API_KEY:-}")" | tee_log
  echo "BEYONDTRUST_BASE_URL=${BEYONDTRUST_BASE_URL:-}" | tee_log
  echo "BEYONDTRUST_API_TOKEN=$(mask_value "${BEYONDTRUST_API_TOKEN:-}")" | tee_log
  echo "BEYONDTRUST_USERNAME=${BEYONDTRUST_USERNAME:-}" | tee_log
  echo "BEYONDTRUST_PASSWORD=$(mask_value "${BEYONDTRUST_PASSWORD:-}")" | tee_log
}

generate_env_template() {
  print_header "10) Generate Template .env File"

  if [[ ! -f "${SCRIPT_DIR}/.env.example" ]]; then
    print_fail ".env.example not found at ${SCRIPT_DIR}/.env.example"
    return
  fi

  cp "${SCRIPT_DIR}/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  print_success "Generated ${ENV_FILE} from .env.example"
}

install_dependencies() {
  print_header "11) Install Python Dependencies"

  local venv_dir="${SCRIPT_DIR}/venv"
  if [[ ! -d "$venv_dir" ]]; then
    python3 -m venv "$venv_dir" || {
      print_fail "Unable to create virtual environment"
      return
    }
    print_success "Created venv at ${venv_dir}"
  else
    print_info "Using existing venv at ${venv_dir}"
  fi

  "${venv_dir}/bin/pip" install -r "$REQUIREMENTS_FILE" >/dev/null 2>&1
  if [[ $? -eq 0 ]]; then
    print_success "Dependencies installed"
  else
    print_fail "Dependency installation failed"
  fi
}

print_summary() {
  print_header "8) Validation Summary"
  echo -e "${GREEN}Passed:${NC}   $TESTS_PASSED" | tee_log
  echo -e "${RED}Failed:${NC}   $TESTS_FAILED" | tee_log
  echo -e "${YELLOW}Warnings:${NC} $TESTS_WARNING" | tee_log

  if [[ $TESTS_FAILED -eq 0 ]]; then
    print_info "Recommended dry-run command:"
    echo "cd ${SCRIPT_DIR} && ./venv/bin/python3 beyondtrust.py --env-file .env --dry-run --save-json --log-level DEBUG" | tee_log
    return 0
  fi

  echo "✗ Some checks failed. Please address the issues above before deployment." | tee_log
  return 1
}

run_all_checks() {
  check_system_requirements
  check_python_dependencies
  check_configuration_file
  check_network_connectivity
  check_api_authentication
  check_api_endpoint_access
  check_deployment_structure
  print_summary
}

show_menu() {
  cat <<'EOF'

Select an option:
1) System Requirements       7) Deployment Structure
2) Python Dependencies       8) Run ALL Checks (recommended)
3) Configuration File        9) Display Current Configuration
4) Network Connectivity     10) Generate Template .env File
5) API Authentication       11) Install Python Dependencies
6) API Endpoint Access       0) Exit
EOF
}

main() {
  : > "$LOG_FILE"
  print_info "Logging to ${LOG_FILE}"

  if [[ "${1:-}" == "--all" ]]; then
    run_all_checks
    exit $?
  fi

  while true; do
    show_menu
    read -r -p "Choice: " choice </dev/tty
    case "$choice" in
      1) check_system_requirements ;;
      2) check_python_dependencies ;;
      3) check_configuration_file ;;
      4) check_network_connectivity ;;
      5) check_api_authentication ;;
      6) check_api_endpoint_access ;;
      7) check_deployment_structure ;;
      8) run_all_checks ;;
      9) display_current_configuration ;;
      10) generate_env_template ;;
      11) install_dependencies ;;
      0) print_info "Exiting"; exit 0 ;;
      *) print_warning "Invalid option" ;;
    esac
  done
}

main "$@"
