#!/usr/bin/env bash
# install_beyondtrust.sh - One-command installer for BeyondTrust-Veza OAA integration

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
SYSTEM_SLUG="beyondtrust"
PY_SCRIPT="beyondtrust.py"
INSTALL_DIR="/opt/VEZA/${SYSTEM_SLUG}-veza"
SCRIPTS_DIR=""
REPO_URL="https://github.com/<org>/<repo>.git"
BRANCH="main"
INTEGRATION_SUBDIR="integrations/${SYSTEM_SLUG}"
NON_INTERACTIVE="false"
OVERWRITE_ENV="false"
OS_ID="unknown"
PKG_MGR=""

info() { printf '[INFO] %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1"; }
die() { printf '[ERROR] %s\n' "$1" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: ${SCRIPT_NAME} [options]

Options:
  --non-interactive         Use env vars for prompts
  --overwrite-env           Overwrite existing .env
  --install-dir <path>      Target install directory (default: ${INSTALL_DIR})
  --repo-url <url>          Git repository URL
  --branch <name>           Git branch to clone (default: ${BRANCH})
  -h, --help                Show help

Non-interactive required env vars:
  VEZA_URL VEZA_API_KEY BEYONDTRUST_BASE_URL
  and one of: BEYONDTRUST_API_TOKEN or (BEYONDTRUST_USERNAME + BEYONDTRUST_PASSWORD)
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --non-interactive)
        NON_INTERACTIVE="true"
        shift
        ;;
      --overwrite-env)
        OVERWRITE_ENV="true"
        shift
        ;;
      --install-dir)
        INSTALL_DIR="$2"
        shift 2
        ;;
      --repo-url)
        REPO_URL="$2"
        shift 2
        ;;
      --branch)
        BRANCH="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done

  SCRIPTS_DIR="${INSTALL_DIR}/scripts"
}

detect_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
  fi

  if command -v dnf >/dev/null 2>&1; then
    PKG_MGR="dnf"
  elif command -v yum >/dev/null 2>&1; then
    PKG_MGR="yum"
  elif command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt-get"
  else
    die "Supported package manager not found (dnf/yum/apt-get)"
  fi

  info "Detected OS_ID=${OS_ID}, package manager=${PKG_MGR}"

  if [[ "${PKG_MGR}" == "apt-get" ]]; then
    sudo apt-get update -y >/dev/null
  fi
}

_install_pkg() {
  local pkg="$1"
  case "${PKG_MGR}" in
    dnf|yum)
      sudo "${PKG_MGR}" install -y "${pkg}" >/dev/null
      ;;
    apt-get)
      sudo apt-get install -y "${pkg}" >/dev/null
      ;;
  esac
}

install_base_packages() {
  command -v git >/dev/null 2>&1 || _install_pkg git
  command -v python3 >/dev/null 2>&1 || _install_pkg python3

  if ! python3 -m pip --version >/dev/null 2>&1; then
    if [[ "${PKG_MGR}" == "apt-get" ]]; then
      _install_pkg python3-pip
    else
      _install_pkg python3-pip
    fi
  fi

  if ! command -v curl >/dev/null 2>&1; then
    if [[ "${OS_ID}" == "amzn" ]]; then
      warn "Skipping curl on Amazon Linux (curl-minimal conflict)"
    else
      _install_pkg curl
    fi
  fi

  if ! python3 -m venv --help >/dev/null 2>&1; then
    case "${PKG_MGR}" in
      dnf|yum)
        _install_pkg python3-virtualenv
        ;;
      apt-get)
        _install_pkg python3-venv
        ;;
    esac
  fi
}

check_python_version() {
  local pyv
  pyv="$(python3 - <<'PYEOF'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PYEOF
)"

  local major minor
  major="${pyv%%.*}"
  minor="${pyv##*.}"

  if [[ "${major}" -lt 3 ]] || { [[ "${major}" -eq 3 ]] && [[ "${minor}" -lt 8 ]]; }; then
    die "Python 3.8+ is required, found ${pyv}"
  fi
}

copy_integration_files() {
  local tmp_dir
  tmp_dir="$(mktemp -d)"

  info "Cloning repository branch ${BRANCH}"
  GIT_TERMINAL_PROMPT=0 git clone --branch "${BRANCH}" --depth 1 --single-branch "${REPO_URL}" "${tmp_dir}" \
    || die "git clone failed"

  mkdir -p "${SCRIPTS_DIR}" "${INSTALL_DIR}/logs"

  cp -f "${tmp_dir}/${INTEGRATION_SUBDIR}/${PY_SCRIPT}" "${SCRIPTS_DIR}/"
  cp -f "${tmp_dir}/${INTEGRATION_SUBDIR}/requirements.txt" "${SCRIPTS_DIR}/"
  cp -f "${tmp_dir}/${INTEGRATION_SUBDIR}/preflight.sh" "${SCRIPTS_DIR}/"
  cp -f "${tmp_dir}/${INTEGRATION_SUBDIR}/.env.example" "${SCRIPTS_DIR}/.env.example"

  chmod +x "${SCRIPTS_DIR}/${PY_SCRIPT}" "${SCRIPTS_DIR}/preflight.sh"

  rm -rf "${tmp_dir}"
}

prompt_value() {
  local label="$1"
  local current="$2"
  local secret="${3:-false}"
  local value="${current}"

  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    printf '%s' "${value}"
    return
  fi

  if [[ "${secret}" == "true" ]]; then
    IFS= read -r -s -p "${label}: " value </dev/tty
    echo >/dev/tty
  else
    IFS= read -r -p "${label}: " value </dev/tty
  fi

  printf '%s' "${value}"
}

create_env_file() {
  local env_file="${SCRIPTS_DIR}/.env"
  if [[ -f "${env_file}" && "${OVERWRITE_ENV}" != "true" ]]; then
    warn ".env already exists; use --overwrite-env to replace"
    return
  fi

  local veza_url veza_api_key bt_base bt_token bt_user bt_pass
  veza_url="$(prompt_value "Veza URL" "${VEZA_URL:-}")"
  veza_api_key="$(prompt_value "Veza API key" "${VEZA_API_KEY:-}" true)"
  bt_base="$(prompt_value "BeyondTrust Base URL" "${BEYONDTRUST_BASE_URL:-}")"
  bt_token="$(prompt_value "BeyondTrust API token (optional if using user/password)" "${BEYONDTRUST_API_TOKEN:-}" true)"
  bt_user="$(prompt_value "BeyondTrust username (optional if using API token)" "${BEYONDTRUST_USERNAME:-}")"
  bt_pass="$(prompt_value "BeyondTrust password (optional if using API token)" "${BEYONDTRUST_PASSWORD:-}" true)"

  if [[ -z "${veza_url}" || -z "${veza_api_key}" || -z "${bt_base}" ]]; then
    die "VEZA_URL, VEZA_API_KEY, and BEYONDTRUST_BASE_URL are required"
  fi

  if [[ -z "${bt_token}" && ( -z "${bt_user}" || -z "${bt_pass}" ) ]]; then
    die "Provide BEYONDTRUST_API_TOKEN or BEYONDTRUST_USERNAME and BEYONDTRUST_PASSWORD"
  fi

  cat > "${env_file}" <<EOF
# BeyondTrust source configuration
BEYONDTRUST_BASE_URL=${bt_base}
BEYONDTRUST_API_TOKEN=${bt_token}
BEYONDTRUST_USERNAME=${bt_user}
BEYONDTRUST_PASSWORD=${bt_pass}
BEYONDTRUST_API_KEY=${BEYONDTRUST_API_KEY:-}

# Optional endpoint overrides
BEYONDTRUST_AUTH_ENDPOINT=/api/public/v3/Auth/SignAppin
BEYONDTRUST_MANAGED_ACCOUNTS_ENDPOINT=/api/public/v3/ManagedAccounts
BEYONDTRUST_DEVICES_ENDPOINT=/api/public/v3/ManagedSystems
BEYONDTRUST_APPLICATIONS_ENDPOINT=/api/public/v3/Applications
BEYONDTRUST_ACCESS_ASSIGNMENTS_ENDPOINT=/api/public/v3/AccessAssignments

# Veza configuration
VEZA_URL=${veza_url}
VEZA_API_KEY=${veza_api_key}

# Optional naming overrides
PROVIDER_NAME=BeyondTrust
DATASOURCE_NAME=BeyondTrust
EOF

  chmod 600 "${env_file}"
  info "Created ${env_file}"
}

setup_venv() {
  python3 -m venv "${SCRIPTS_DIR}/venv"
  "${SCRIPTS_DIR}/venv/bin/pip" install --upgrade pip >/dev/null
  "${SCRIPTS_DIR}/venv/bin/pip" install -r "${SCRIPTS_DIR}/requirements.txt" >/dev/null
}

main() {
  parse_args "$@"
  detect_os
  install_base_packages
  check_python_version
  copy_integration_files
  create_env_file
  setup_venv

  cat <<EOF

Installation complete.
Install path: ${INSTALL_DIR}
Script path:  ${SCRIPTS_DIR}/${PY_SCRIPT}

Next steps:
1) cd ${SCRIPTS_DIR}
2) ./preflight.sh --all
3) ./venv/bin/python3 ${PY_SCRIPT} --env-file .env --dry-run --save-json --log-level DEBUG

EOF
}

main "$@"
