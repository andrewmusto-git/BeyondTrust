#!/usr/bin/env python3
"""
BeyondTrust to Veza OAA integration.
Collects managed accounts, devices, applications, and access assignments from BeyondTrust,
then builds and optionally pushes a Veza CustomApplication payload.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from oaaclient.client import OAAClient, OAAClientError
from oaaclient.templates import OAAPermission, CustomApplication


log = logging.getLogger(__name__)


def _setup_logging(log_level: str = "INFO") -> None:
    """Configure file-only logging with hourly rotation to the logs/ folder."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%d%m%Y-%H%M")
    script_name = os.path.splitext(os.path.basename(__file__))[0]
    log_file = os.path.join(log_dir, f"{script_name}_{timestamp}.log")

    handler = TimedRotatingFileHandler(
        log_file,
        when="h",
        interval=1,
        backupCount=24,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root.addHandler(handler)


@dataclass
class Config:
    veza_url: Optional[str]
    veza_api_key: Optional[str]
    provider_name: str
    datasource_name: str
    bt_host_url: str
    bt_base_url: str
    bt_api_token: Optional[str]
    bt_api_key: Optional[str]
    bt_auth_type: str
    bt_oauth_grant_type: str
    bt_oauth_token_url: Optional[str]
    bt_oauth_client_id: Optional[str]
    bt_oauth_client_secret: Optional[str]
    bt_oauth_scope: Optional[str]
    bt_username: Optional[str]
    bt_password: Optional[str]
    bt_auth_endpoint: str
    managed_accounts_endpoint: str
    devices_endpoint: str
    applications_endpoint: str
    access_assignments_endpoint: str
    users_endpoint: Optional[str]
    groups_endpoint: Optional[str]
    timeout_seconds: int
    verify_tls: bool
    additional_headers: Dict[str, str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BeyondTrust Veza OAA connector")

    parser.add_argument("--data-dir", default="./samples", help="Directory for source sample or helper data files")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--dry-run", action="store_true", help="Build payload without pushing to Veza")
    parser.add_argument("--save-json", action="store_true", help="Save generated payload JSON to disk")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    parser.add_argument("--provider-name", default="BeyondTrust", help="Provider name in Veza")
    parser.add_argument("--datasource-name", default="BeyondTrust", help="Datasource name in Veza")

    parser.add_argument("--veza-url", default=None, help="Veza tenant URL")
    parser.add_argument("--veza-api-key", default=None, help="Veza API key")

    parser.add_argument("--bt-host-url", default=None, help="BeyondTrust host URL")
    parser.add_argument("--bt-base-url", default=None, help="BeyondTrust API base URL (legacy alias)")
    parser.add_argument("--bt-api-token", default=None, help="BeyondTrust bearer token")
    parser.add_argument("--bt-api-key", default=None, help="BeyondTrust API key header value")
    parser.add_argument("--bt-auth-type", default=None, help="Auth type: oauth2_client_credentials|token|username_password")
    parser.add_argument("--bt-oauth-grant-type", default=None, help="OAuth grant type (default: client_credentials)")
    parser.add_argument("--bt-oauth-token-url", default=None, help="OAuth token endpoint URL")
    parser.add_argument("--bt-oauth-client-id", default=None, help="OAuth client ID")
    parser.add_argument("--bt-oauth-client-secret", default=None, help="OAuth client secret")
    parser.add_argument("--bt-oauth-scope", default=None, help="OAuth scope (optional)")
    parser.add_argument("--bt-username", default=None, help="BeyondTrust username for login auth")
    parser.add_argument("--bt-password", default=None, help="BeyondTrust password for login auth")

    parser.add_argument("--bt-auth-endpoint", default="/api/public/v3/Auth/SignAppin", help="Auth endpoint for token minting")
    parser.add_argument(
        "--managed-accounts-endpoint",
        default="/api/public/v3/ManagedAccounts",
        help="Endpoint to list managed accounts",
    )
    parser.add_argument(
        "--devices-endpoint",
        default="/api/public/v3/ManagedSystems",
        help="Endpoint to list managed devices/systems",
    )
    parser.add_argument(
        "--applications-endpoint",
        default="/api/public/v3/Applications",
        help="Endpoint to list managed applications",
    )
    parser.add_argument(
        "--access-assignments-endpoint",
        default="/api/public/v3/AccessAssignments",
        help="Endpoint to list principal access assignments",
    )
    parser.add_argument("--users-endpoint", default="", help="Optional endpoint to list users")
    parser.add_argument("--groups-endpoint", default="", help="Optional endpoint to list groups")

    parser.add_argument("--timeout-seconds", type=int, default=30, help="HTTP timeout for BeyondTrust requests")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for API calls")
    parser.add_argument(
        "--additional-headers-json",
        default="",
        help='Optional JSON object of extra headers, e.g. {"X-Custom":"value"}',
    )

    return parser.parse_args()


def _load_json_dict(raw: str) -> Dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for additional headers: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("additional-headers-json must be a JSON object")

    result: Dict[str, str] = {}
    for key, value in data.items():
        result[str(key)] = str(value)
    return result


def load_config(args: argparse.Namespace) -> Config:
    if args.env_file and os.path.exists(args.env_file):
        load_dotenv(args.env_file)

    host_url = (
        args.bt_host_url
        or os.getenv("BEYONDTRUST_HOST_URL")
        or args.bt_base_url
        or os.getenv("BEYONDTRUST_BASE_URL", "")
    ).rstrip("/")

    return Config(
        veza_url=args.veza_url or os.getenv("VEZA_URL"),
        veza_api_key=args.veza_api_key or os.getenv("VEZA_API_KEY"),
        provider_name=args.provider_name or os.getenv("PROVIDER_NAME", "BeyondTrust"),
        datasource_name=args.datasource_name or os.getenv("DATASOURCE_NAME", "BeyondTrust"),
        bt_host_url=host_url,
        bt_base_url=host_url,
        bt_api_token=args.bt_api_token or os.getenv("BEYONDTRUST_API_TOKEN"),
        bt_api_key=args.bt_api_key or os.getenv("BEYONDTRUST_API_KEY"),
        bt_auth_type=(args.bt_auth_type or os.getenv("BEYONDTRUST_AUTH_TYPE", "")).strip().lower() or "token",
        bt_oauth_grant_type=(
            args.bt_oauth_grant_type or os.getenv("BEYONDTRUST_OAUTH_GRANT_TYPE", "client_credentials")
        ).strip(),
        bt_oauth_token_url=args.bt_oauth_token_url or os.getenv("BEYONDTRUST_OAUTH_TOKEN_URL"),
        bt_oauth_client_id=args.bt_oauth_client_id or os.getenv("BEYONDTRUST_OAUTH_CLIENT_ID"),
        bt_oauth_client_secret=args.bt_oauth_client_secret or os.getenv("BEYONDTRUST_OAUTH_CLIENT_SECRET"),
        bt_oauth_scope=args.bt_oauth_scope or os.getenv("BEYONDTRUST_OAUTH_SCOPE"),
        bt_username=args.bt_username or os.getenv("BEYONDTRUST_USERNAME"),
        bt_password=args.bt_password or os.getenv("BEYONDTRUST_PASSWORD"),
        bt_auth_endpoint=args.bt_auth_endpoint or os.getenv("BEYONDTRUST_AUTH_ENDPOINT", "/api/public/v3/Auth/SignAppin"),
        managed_accounts_endpoint=(
            args.managed_accounts_endpoint
            or os.getenv("BEYONDTRUST_MANAGED_ACCOUNTS_ENDPOINT", "/api/public/v3/ManagedAccounts")
        ),
        devices_endpoint=(
            args.devices_endpoint
            or os.getenv("BEYONDTRUST_DEVICES_ENDPOINT", "/api/public/v3/ManagedSystems")
        ),
        applications_endpoint=(
            args.applications_endpoint
            or os.getenv("BEYONDTRUST_APPLICATIONS_ENDPOINT", "/api/public/v3/Applications")
        ),
        access_assignments_endpoint=(
            args.access_assignments_endpoint
            or os.getenv("BEYONDTRUST_ACCESS_ASSIGNMENTS_ENDPOINT", "/api/public/v3/AccessAssignments")
        ),
        users_endpoint=(args.users_endpoint or os.getenv("BEYONDTRUST_USERS_ENDPOINT", "")).strip() or None,
        groups_endpoint=(args.groups_endpoint or os.getenv("BEYONDTRUST_GROUPS_ENDPOINT", "")).strip() or None,
        timeout_seconds=args.timeout_seconds,
        verify_tls=not args.insecure,
        additional_headers=_load_json_dict(args.additional_headers_json or os.getenv("BEYONDTRUST_ADDITIONAL_HEADERS_JSON", "")),
    )


def _first_present(record: Dict[str, Any], keys: Iterable[str], default: str = "") -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip() != "":
            return str(value)
    return default


def _as_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("value", "values", "items", "data", "result", "Results", "records"):
            maybe = payload.get(key)
            if isinstance(maybe, list):
                return [item for item in maybe if isinstance(item, dict)]

    return []


def _read_csv_records(csv_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def _csv_samples_exist(data_dir: str) -> bool:
    expected = ["BTManagedAccounts.csv", "BTManagedSystems.csv", "BTAssets.csv"]
    return all(os.path.exists(os.path.join(data_dir, name)) for name in expected)


def _is_true(record: Dict[str, Any], key: str) -> bool:
    value = str(record.get(key, "")).strip().lower()
    return value in {"true", "1", "yes", "y"}


def _permissions_from_managed_account(account: Dict[str, Any]) -> List[str]:
    # Start with baseline visibility for any discovered managed account.
    permissions: List[str] = ["use"]

    checkout_signals = (
        _is_true(account, "CheckPasswordFlag")
        or _is_true(account, "UseSelfFlag")
        or _is_true(account, "ChangePasswordAfterAnyReleaseFlag")
    )
    manage_signals = (
        _is_true(account, "ManageableFlag")
        or _is_true(account, "AutoManagementFlag")
        or _is_true(account, "SystemAutoManagementFlag")
        or _is_true(account, "ResetPasswordOnMismatchFlag")
    )
    admin_signals = (
        _is_true(account, "ApiEnabled")
        or _is_true(account, "RNSSEnabledFlag")
    )

    if checkout_signals:
        permissions.append("checkout")
    if manage_signals:
        permissions.append("manage")
    if admin_signals:
        permissions.append("admin")

    # Ensure deterministic order to keep payload output stable run-to-run.
    ordered = ["use", "checkout", "manage", "admin"]
    return [perm for perm in ordered if perm in permissions]


def _highest_permission(permissions: List[str]) -> str:
    if "admin" in permissions:
        return "admin"
    if "manage" in permissions:
        return "manage"
    if "checkout" in permissions:
        return "checkout"
    return "use"


def collect_from_csv(data_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    managed_accounts = _read_csv_records(os.path.join(data_dir, "BTManagedAccounts.csv"))
    devices = _read_csv_records(os.path.join(data_dir, "BTManagedSystems.csv"))
    assets = _read_csv_records(os.path.join(data_dir, "BTAssets.csv"))

    applications: List[Dict[str, Any]] = []
    access_assignments: List[Dict[str, Any]] = []
    users: List[Dict[str, Any]] = []
    groups: List[Dict[str, Any]] = []

    seen_apps: set[str] = set()
    for asset in assets:
        asset_id = _first_present(asset, ["AssetID", "assetId", "id"])
        asset_name = _first_present(asset, ["AssetName", "assetName", "name"], default=asset_id)
        if not asset_id:
            continue
        if asset_id in seen_apps:
            continue
        seen_apps.add(asset_id)
        applications.append(
            {
                "applicationId": asset_id,
                "applicationName": asset_name,
                "platformName": _first_present(asset, ["PlatformName", "platformName"]),
                "dnsName": _first_present(asset, ["DnsName", "dnsName"]),
                "ipAddress": _first_present(asset, ["IPAddress", "ipAddress"]),
            }
        )

    group_index: Dict[str, Dict[str, str]] = {}

    for account in managed_accounts:
        managed_account_id = _first_present(account, ["ManagedAccountID", "managedAccountId", "id"])
        account_name = _first_present(account, ["AccountName", "accountName", "name"], default=managed_account_id)
        managed_system_id = _first_present(account, ["ManagedSystemID", "managedSystemId", "systemId"])
        asset_id = _first_present(account, ["AssetID", "assetId"])

        if not managed_account_id:
            continue

        group_ids: List[str] = []
        domain_name = _first_present(account, ["DomainName", "domainName"])
        workgroup_name = _first_present(account, ["WorkgroupName", "workGroupName"])

        if domain_name:
            gid = f"domain:{domain_name.lower()}"
            group_index[gid] = {"groupId": gid, "groupName": f"Domain:{domain_name}"}
            group_ids.append(gid)
        if workgroup_name:
            gid = f"workgroup:{workgroup_name.lower()}"
            group_index[gid] = {"groupId": gid, "groupName": f"Workgroup:{workgroup_name}"}
            group_ids.append(gid)

        users.append(
            {
                "userId": managed_account_id,
                "userName": account_name,
                "groupIds": ";".join(group_ids),
                "domainName": domain_name,
                "workgroupName": workgroup_name,
            }
        )

        account_permissions = _permissions_from_managed_account(account)
        account_permission = _highest_permission(account_permissions)

        access_assignments.append(
            {
                "principalType": "user",
                "principalId": managed_account_id,
                "principalName": account_name,
                "resourceType": "managed_account",
                "resourceId": managed_account_id,
                "permission": account_permission,
            }
        )

        if managed_system_id:
            # Device access is capability-based; we cap it at manage for host scope.
            device_permission = "manage" if account_permission in {"manage", "admin"} else "use"
            access_assignments.append(
                {
                    "principalType": "user",
                    "principalId": managed_account_id,
                    "principalName": account_name,
                    "resourceType": "device",
                    "resourceId": managed_system_id,
                    "permission": device_permission,
                }
            )

        if asset_id:
            # Application access aligns with checkout workflows when enabled.
            app_permission = "checkout" if account_permission in {"checkout", "manage", "admin"} else "use"
            access_assignments.append(
                {
                    "principalType": "user",
                    "principalId": managed_account_id,
                    "principalName": account_name,
                    "resourceType": "application",
                    "resourceId": asset_id,
                    "permission": app_permission,
                }
            )

    groups = list(group_index.values())

    return {
        "managed_accounts": managed_accounts,
        "devices": devices,
        "applications": applications,
        "access_assignments": access_assignments,
        "users": users,
        "groups": groups,
    }


class BeyondTrustClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.session.headers.update(config.additional_headers)

    def _url(self, endpoint: str) -> str:
        endpoint = endpoint.strip()
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        return f"{self.config.bt_base_url}/{endpoint.lstrip('/')}"

    def authenticate(self) -> None:
        if self.config.bt_api_token:
            self.session.headers["Authorization"] = f"Bearer {self.config.bt_api_token}"
            log.debug("Using static BeyondTrust bearer token from configuration")

        if self.config.bt_api_key:
            self.session.headers["X-API-Key"] = self.config.bt_api_key

        if "Authorization" in self.session.headers:
            return

        if self.config.bt_auth_type == "oauth2_client_credentials":
            if not (
                self.config.bt_oauth_token_url
                and self.config.bt_oauth_client_id
                and self.config.bt_oauth_client_secret
            ):
                raise RuntimeError(
                    "OAuth2 client credentials selected, but token URL/client ID/client secret are missing"
                )

            payload = {
                "grant_type": self.config.bt_oauth_grant_type or "client_credentials",
                "client_id": self.config.bt_oauth_client_id,
                "client_secret": self.config.bt_oauth_client_secret,
            }
            if self.config.bt_oauth_scope:
                payload["scope"] = self.config.bt_oauth_scope

            log.info("Requesting OAuth2 access token from token endpoint")
            response = self.session.post(
                self.config.bt_oauth_token_url,
                data=payload,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
            if not response.ok:
                raise RuntimeError(
                    f"OAuth token request failed ({response.status_code}): {response.text[:400]}"
                )

            token_payload = response.json() if response.text else {}
            token = _first_present(token_payload, ["access_token", "token", "bearerToken", "sessionToken"])
            if not token:
                raise RuntimeError("OAuth token response did not include access_token")

            self.session.headers["Authorization"] = f"Bearer {token}"
            return

        if self.config.bt_username and self.config.bt_password:
            auth_url = self._url(self.config.bt_auth_endpoint)
            body = {"username": self.config.bt_username, "password": self.config.bt_password}
            log.info("Authenticating to BeyondTrust API")
            response = self.session.post(
                auth_url,
                json=body,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
            if not response.ok:
                raise RuntimeError(
                    f"BeyondTrust authentication failed ({response.status_code}): {response.text[:400]}"
                )
            token_payload = response.json() if response.text else {}
            token = _first_present(token_payload, ["access_token", "token", "bearerToken", "sessionToken"])
            if not token:
                raise RuntimeError("BeyondTrust auth succeeded but no token field was returned")
            self.session.headers["Authorization"] = f"Bearer {token}"
            return

        raise RuntimeError(
            "Missing BeyondTrust credentials. Provide BEYONDTRUST_API_TOKEN or username/password."
        )

    def _request_json(self, endpoint: str) -> List[Dict[str, Any]]:
        url = self._url(endpoint)
        retries = 3
        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=self.config.timeout_seconds,
                    verify=self.config.verify_tls,
                )
            except requests.RequestException as exc:
                if attempt == retries:
                    raise RuntimeError(f"Request failed for {url}: {exc}") from exc
                sleep_seconds = attempt * 2
                log.warning("Request error for %s (attempt %s/%s): %s", url, attempt, retries, exc)
                time.sleep(sleep_seconds)
                continue

            if response.ok:
                if not response.text:
                    return []
                try:
                    return _as_list(response.json())
                except ValueError as exc:
                    raise RuntimeError(f"Invalid JSON from {url}: {exc}") from exc

            if response.status_code >= 500 and attempt < retries:
                sleep_seconds = attempt * 2
                log.warning(
                    "Server error %s from %s (attempt %s/%s)",
                    response.status_code,
                    url,
                    attempt,
                    retries,
                )
                time.sleep(sleep_seconds)
                continue

            raise RuntimeError(
                f"BeyondTrust API call failed for {url} ({response.status_code}): {response.text[:400]}"
            )

        return []

    def collect(self) -> Dict[str, List[Dict[str, Any]]]:
        self.authenticate()
        data: Dict[str, List[Dict[str, Any]]] = {
            "managed_accounts": self._request_json(self.config.managed_accounts_endpoint),
            "devices": self._request_json(self.config.devices_endpoint),
            "applications": self._request_json(self.config.applications_endpoint),
            "access_assignments": self._request_json(self.config.access_assignments_endpoint),
            "users": [],
            "groups": [],
        }
        if self.config.users_endpoint:
            data["users"] = self._request_json(self.config.users_endpoint)
        if self.config.groups_endpoint:
            data["groups"] = self._request_json(self.config.groups_endpoint)
        return data


def _normalize_principal(assignment: Dict[str, Any]) -> Tuple[str, str, str]:
    principal_type = _first_present(
        assignment,
        [
            "principalType",
            "subjectType",
            "granteeType",
            "actorType",
            "memberType",
        ],
        default="user",
    ).lower()

    principal_id = _first_present(
        assignment,
        [
            "principalId",
            "subjectId",
            "granteeId",
            "actorId",
            "memberId",
            "userId",
            "groupId",
        ],
    )
    principal_name = _first_present(
        assignment,
        [
            "principalName",
            "subjectName",
            "granteeName",
            "actorName",
            "memberName",
            "userName",
            "groupName",
            "displayName",
            "name",
        ],
        default=principal_id,
    )

    if not principal_id:
        principal_id = principal_name

    if principal_type not in {"group", "role"}:
        principal_type = "user"

    return principal_type, principal_id, principal_name


def _normalize_resource(assignment: Dict[str, Any]) -> Tuple[str, str]:
    resource_type = _first_present(
        assignment,
        ["resourceType", "targetType", "objectType", "entityType"],
        default="managed_account",
    ).lower()
    resource_id = _first_present(
        assignment,
        ["resourceId", "targetId", "objectId", "entityId", "managedAccountId", "systemId", "applicationId"],
    )
    return resource_type, resource_id


def _normalize_permission(assignment: Dict[str, Any]) -> str:
    raw = _first_present(
        assignment,
        ["permission", "permissionName", "entitlement", "accessLevel", "role", "accessType"],
        default="use",
    ).strip()
    if not raw:
        return "use"
    lowered = raw.lower()
    if lowered in {"checkout", "check_out"}:
        return "checkout"
    if lowered in {"manage", "manager", "owner"}:
        return "manage"
    if lowered in {"admin", "administrator", "superuser"}:
        return "admin"
    return "use"


def log_permission_summary(data: Dict[str, List[Dict[str, Any]]]) -> None:
    assignments = data.get("access_assignments", [])
    counters: Dict[str, int] = {
        "use": 0,
        "checkout": 0,
        "manage": 0,
        "admin": 0,
    }
    resource_counters: Dict[str, int] = {
        "managed_account": 0,
        "device": 0,
        "application": 0,
        "other": 0,
    }

    for assignment in assignments:
        permission = _normalize_permission(assignment)
        counters[permission] = counters.get(permission, 0) + 1

        resource_type = _normalize_resource(assignment)[0]
        if resource_type in {"managed_account", "device", "application"}:
            resource_counters[resource_type] += 1
        else:
            resource_counters["other"] += 1

    total = sum(counters.values())
    if total == 0:
        log.info("Permission summary: no access assignments discovered")
        return

    summary_parts = []
    for perm in ["use", "checkout", "manage", "admin"]:
        count = counters.get(perm, 0)
        pct = (count / total) * 100.0
        summary_parts.append(f"{perm}={count} ({pct:.1f}%)")

    resource_parts = []
    for resource_type in ["managed_account", "device", "application", "other"]:
        count = resource_counters.get(resource_type, 0)
        if count > 0:
            resource_parts.append(f"{resource_type}={count}")

    log.info("Permission summary: total=%s | %s", total, " | ".join(summary_parts))
    if resource_parts:
        log.info("Assignment scope: %s", " | ".join(resource_parts))


def build_oaa_payload(data: Dict[str, List[Dict[str, Any]]], args: argparse.Namespace) -> CustomApplication:
    app = CustomApplication(name=args.datasource_name, application_type=args.provider_name)

    app.add_custom_permission("use", [OAAPermission.DataRead])
    app.add_custom_permission("checkout", [OAAPermission.DataRead, OAAPermission.NonData])
    app.add_custom_permission("manage", [OAAPermission.DataRead, OAAPermission.DataWrite, OAAPermission.MetadataRead])
    app.add_custom_permission(
        "admin",
        [OAAPermission.DataRead, OAAPermission.DataWrite, OAAPermission.MetadataRead, OAAPermission.MetadataWrite],
    )

    role_to_perms: Dict[str, List[str]] = {
        "role-use": ["use"],
        "role-checkout": ["use", "checkout"],
        "role-manage": ["use", "checkout", "manage"],
        "role-admin": ["use", "checkout", "manage", "admin"],
    }
    for role_id, perms in role_to_perms.items():
        if role_id not in app.local_roles:
            app.add_local_role(name=role_id, unique_id=role_id, permissions=perms)

    resource_index: Dict[str, Any] = {}

    for account in data.get("managed_accounts", []):
        account_id = _first_present(account, ["ManagedAccountID", "id", "managedAccountId", "accountId", "ID"])
        account_name = _first_present(account, ["AccountName", "name", "accountName", "displayName", "username"], default=account_id)
        if not account_id:
            continue
        resource_name = f"ManagedAccount:{account_name}"
        if resource_name not in app.resources:
            app.add_resource(name=resource_name, resource_type="managed_account")
        resource_index[f"managed_account:{account_id}"] = app.resources[resource_name]

    for device in data.get("devices", []):
        device_id = _first_present(device, ["ManagedSystemID", "id", "managedSystemId", "systemId", "deviceId", "ID"])
        device_name = _first_present(device, ["SystemName", "name", "hostname", "dnsName", "displayName"], default=device_id)
        if not device_id:
            continue
        resource_name = f"Device:{device_name}"
        if resource_name not in app.resources:
            app.add_resource(name=resource_name, resource_type="device")
        resource_index[f"device:{device_id}"] = app.resources[resource_name]

    for app_record in data.get("applications", []):
        app_id = _first_present(app_record, ["id", "applicationId", "ID"])
        app_name = _first_present(app_record, ["name", "applicationName", "displayName"], default=app_id)
        if not app_id:
            continue
        resource_name = f"Application:{app_name}"
        if resource_name not in app.resources:
            app.add_resource(name=resource_name, resource_type="application")
        resource_index[f"application:{app_id}"] = app.resources[resource_name]

    for user in data.get("users", []):
        user_id = _first_present(user, ["id", "userId", "principalId", "ID"])
        user_name = _first_present(user, ["name", "userName", "displayName", "emailAddress"], default=user_id)
        if user_id and user_id not in app.local_users:
            app.add_local_user(name=user_name, unique_id=user_id)

    for group in data.get("groups", []):
        group_id = _first_present(group, ["id", "groupId", "principalId", "ID"])
        group_name = _first_present(group, ["name", "groupName", "displayName"], default=group_id)
        if group_id and group_id not in app.local_groups:
            app.add_local_group(name=group_name, unique_id=group_id)

    for user in data.get("users", []):
        user_id = _first_present(user, ["id", "userId", "principalId", "ID"])
        if not user_id or user_id not in app.local_users:
            continue
        for group_id in _first_present(user, ["groupIds"], default="").split(";"):
            gid = group_id.strip()
            if not gid:
                continue
            if gid in app.local_groups:
                app.local_users[user_id].add_group(gid)

    for assignment in data.get("access_assignments", []):
        principal_type, principal_id, principal_name = _normalize_principal(assignment)
        resource_type, resource_id = _normalize_resource(assignment)
        permission = _normalize_permission(assignment)

        if not principal_id or not resource_id:
            continue

        role_id = {
            "use": "role-use",
            "checkout": "role-checkout",
            "manage": "role-manage",
            "admin": "role-admin",
        }.get(permission, "role-use")

        resource_key = f"{resource_type}:{resource_id}"
        resource_obj = resource_index.get(resource_key)
        if resource_obj is None:
            continue

        if principal_type == "group":
            if principal_id not in app.local_groups:
                app.add_local_group(name=principal_name, unique_id=principal_id)
            app.local_groups[principal_id].add_role(role=role_id, resources=[resource_obj])
        else:
            if principal_id not in app.local_users:
                app.add_local_user(name=principal_name, unique_id=principal_id)
            app.local_users[principal_id].add_role(role=role_id, resources=[resource_obj])

    return app


def _save_payload_json(app: CustomApplication, output_dir: str) -> str:
    payload_obj: Any
    if hasattr(app, "to_json"):
        payload_obj = app.to_json()
        if isinstance(payload_obj, str):
            payload_obj = json.loads(payload_obj)
    elif hasattr(app, "to_dict"):
        payload_obj = app.to_dict()
    else:
        raise RuntimeError("Unable to serialize OAA payload: missing to_json()/to_dict()")

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"beyondtrust_payload_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload_obj, handle, indent=2)
    return output_path


def push_to_veza(config: Config, app: CustomApplication, dry_run: bool, save_json: bool, output_dir: str) -> None:
    if save_json:
        json_path = _save_payload_json(app, output_dir)
        log.info("Saved payload JSON to %s", json_path)

    if dry_run:
        log.info("[DRY RUN] Payload built successfully, skipping Veza push")
        return

    if not config.veza_url or not config.veza_api_key:
        raise RuntimeError("VEZA_URL and VEZA_API_KEY are required when not using --dry-run")

    try:
        try:
            veza_con = OAAClient(url=config.veza_url, token=config.veza_api_key)
        except TypeError:
            veza_con = OAAClient(url=config.veza_url, api_key=config.veza_api_key)

        response = veza_con.push_application(
            provider_name=config.provider_name,
            data_source_name=config.datasource_name,
            application_object=app,
            save_json=False,
            create_provider=True,
        )

        for warning in response.get("warnings", []):
            log.warning("Veza warning: %s", warning)
        log.info("Successfully pushed BeyondTrust payload to Veza")
    except OAAClientError as exc:
        log.error("Veza push failed: %s - %s (HTTP %s)", exc.error, exc.message, exc.status_code)
        if hasattr(exc, "details"):
            for detail in exc.details:
                log.error("Detail: %s", detail)
        raise


def validate_required(config: Config, dry_run: bool, use_csv_samples: bool) -> None:
    missing: List[str] = []
    if not use_csv_samples:
        if not config.bt_host_url:
            missing.append("BEYONDTRUST_HOST_URL")
        oauth_ready = bool(
            config.bt_auth_type == "oauth2_client_credentials"
            and config.bt_oauth_token_url
            and config.bt_oauth_client_id
            and config.bt_oauth_client_secret
        )
        legacy_ready = bool(config.bt_api_token or (config.bt_username and config.bt_password))
        if not (oauth_ready or legacy_ready):
            missing.append(
                "OAuth2 client credentials (token URL/client ID/client secret) or BEYONDTRUST_API_TOKEN or BEYONDTRUST_USERNAME/BEYONDTRUST_PASSWORD"
            )
    if not dry_run:
        if not config.veza_url:
            missing.append("VEZA_URL")
        if not config.veza_api_key:
            missing.append("VEZA_API_KEY")

    if missing:
        raise ValueError("Missing required configuration: " + ", ".join(missing))


def main() -> int:
    args = parse_args()
    _setup_logging(args.log_level)

    try:
        config = load_config(args)

        use_csv_samples = _csv_samples_exist(args.data_dir)
        validate_required(config, args.dry_run, use_csv_samples)

        if args.data_dir:
            log.info("Using data-dir: %s", args.data_dir)

        if use_csv_samples:
            log.info("Using CSV sample modeling mode from %s", args.data_dir)
            data = collect_from_csv(args.data_dir)
        else:
            bt_client = BeyondTrustClient(config)
            data = bt_client.collect()
        log.info(
            "Fetched records - managed_accounts=%s devices=%s applications=%s assignments=%s users=%s groups=%s",
            len(data.get("managed_accounts", [])),
            len(data.get("devices", [])),
            len(data.get("applications", [])),
            len(data.get("access_assignments", [])),
            len(data.get("users", [])),
            len(data.get("groups", [])),
        )
        log_permission_summary(data)

        app = build_oaa_payload(data, args)
        push_to_veza(config, app, args.dry_run, args.save_json, output_dir=args.data_dir or ".")

        log.info("Run completed")
        return 0
    except Exception as exc:
        log.error("Run failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
