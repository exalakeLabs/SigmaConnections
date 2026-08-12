#!/usr/bin/env python3
"""Add a catalog and schema to a Sigma connection's writeback locations.

Sigma's supported connection update endpoint uses PUT, so it requires the full
connection payload. The script retrieves the current connection, converts that
response into an update payload, appends the requested location, and submits the
complete result. Only secrets omitted by the GET endpoint are supplied separately.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from typing import Any

try:
    import requests
except ModuleNotFoundError:  # Give CLI users a useful message instead of an import traceback.
    requests = None  # type: ignore[assignment]


DEFAULT_BASE_URL = "https://aws-api.sigmacomputing.com"
CATALOG_CONNECTION_TYPES = {"databricks"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-name", required=True)
    parser.add_argument("--catalog", required=True, help="Databricks writeback catalog")
    parser.add_argument("--schema", required=True)
    parser.add_argument("--output-payload", help="Optionally save the generated payload as JSON")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Retrieve the connection and print the payload without updating it",
    )
    return parser.parse_args()


def _request_location(entry: dict[str, Any], connection_type: str) -> dict[str, Any]:
    """Convert a GET writeback entry to the shape accepted by connection PUT."""
    database_key = "writeCatalog" if connection_type in CATALOG_CONNECTION_TYPES else "writeDatabase"
    database = entry.get(database_key) or entry.get("database") or entry.get("catalog")
    schema = entry.get("writeSchema") or entry.get("schema")
    if not database or not schema:
        raise ValueError(f"Retrieved writeback entry is incomplete: {entry!r}")
    location = {database_key: database, "writeSchema": schema}
    if entry.get("description") is not None:
        location["description"] = entry["description"]
    return location


def build_update_payload(
    current: dict[str, Any], catalog: str, schema: str, oauth_client_secret: str = ""
) -> dict[str, Any]:
    """Build a PUT payload from a fetched connection and append a writeback location."""
    if not catalog.strip() or not schema.strip():
        raise ValueError("Catalog and schema cannot be empty.")

    name = current.get("name")
    connection_type = str(current.get("type", "")).lower()
    if not isinstance(name, str) or not name.strip() or not connection_type:
        raise ValueError("Retrieved connection is missing its name or type.")
    if connection_type != "databricks":
        raise ValueError("Automatic payload construction currently supports Databricks connections only.")

    host = current.get("account") or current.get("host")
    endpoint = current.get("warehouse") or current.get("endpoint")
    if not host:
        raise ValueError("Retrieved Databricks connection is missing its host/account value.")

    details: dict[str, Any] = {
        "type": connection_type,
        "host": host,
        "useOauth": bool(current.get("useOauth")),
    }
    if endpoint is not None:
        details["endpoint"] = endpoint

    user_attributes = current.get("userAttributes")
    if user_attributes is not None:
        if not isinstance(user_attributes, dict):
            raise ValueError("Retrieved userAttributes must be an object.")
        if user_attributes:
            details["userAttributes"] = deepcopy(user_attributes)

    for field in (
        "materializationWarehouse",
        "exportsWarehouse",
        "inputTableAuditLogSchema",
        "roleSwitching",
    ):
        if current.get(field) is not None:
            details[field] = deepcopy(current[field])

    if details["useOauth"]:
        independent_oauth = bool(current.get("isIndependentOAuth"))
        details["useOrgOauth"] = not independent_oauth
        if independent_oauth:
            if not oauth_client_secret:
                raise ValueError(
                    "Set DATABRICKS_OAUTH_CLIENT_SECRET because Sigma does not return it from GET."
                )
            oauth: dict[str, Any] = {
                "provider": "databricks",
                "clientId": current.get("oauthClientId"),
                "clientSecret": {"type": "plain", "value": oauth_client_secret},
                "metadataUrl": current.get("oauthMetadataUrl"),
                "scopes": current.get("oauthScopes") or [],
            }
            if current.get("oauthIdpType") is not None:
                oauth["idpType"] = current["oauthIdpType"]
            if current.get("oauthUsePkce") is not None:
                oauth["usePkce"] = current["oauthUsePkce"]
            if current.get("oauthUseJwt") is not None:
                oauth["useJwt"] = current["oauthUseJwt"]
            if current.get("oauthAudience") is not None:
                oauth["audience"] = current["oauthAudience"]
            if not oauth["clientId"] or not oauth["metadataUrl"]:
                raise ValueError("Retrieved connection is missing its OAuth client ID or metadata URL.")
            details["oauth"] = oauth

    database_key = "writeCatalog" if connection_type in CATALOG_CONNECTION_TYPES else "writeDatabase"
    location = {database_key: catalog, "writeSchema": schema}

    def already_present(locations: list[dict[str, Any]]) -> bool:
        return any(
            entry.get(database_key) == catalog and entry.get("writeSchema") == schema
            for entry in locations
        )

    if details["useOauth"]:
        fetched = current.get("writebackSchemas", [])
        if not isinstance(fetched, list):
            raise ValueError("Retrieved writebackSchemas must be an array.")
        schemas = [_request_location(entry, connection_type) for entry in fetched]
        if already_present(schemas):
            raise ValueError(f"Writeback location {catalog}.{schema} already exists.")
        schemas.append(location)
        details["writebackSchemas"] = schemas
    else:
        fetched = current.get("writebacks", [])
        if not isinstance(fetched, list):
            raise ValueError("Retrieved writebacks must be an array.")
        writebacks = [_request_location(entry, connection_type) for entry in fetched]
        if already_present(writebacks):
            raise ValueError(f"Writeback location {catalog}.{schema} already exists.")
        writebacks.append(location)
        details["writeAccess"] = writebacks

    payload: dict[str, Any] = {"name": name, "details": details}
    if current.get("description") is not None:
        payload["description"] = deepcopy(current["description"])
    if current.get("poolSizes") is not None:
        payload["poolSizes"] = deepcopy(current["poolSizes"])
    timeout = current.get("timeout")
    if isinstance(timeout, dict) and timeout.get("default") is not None:
        payload["timeoutSecs"] = timeout["default"]
    if current.get("friendlyName") is not None:
        payload["useFriendlyNames"] = current["friendlyName"]
    return payload


def get_access_token(base_url: str, client_id: str, client_secret: str) -> str:
    if requests is None:
        raise RuntimeError("Missing dependency: install it with 'python -m pip install requests'.")
    response = requests.post(
        f"{base_url}/v2/auth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    response.raise_for_status()
    token = response.json().get("access_token") or response.json().get("accessToken")
    if not token:
        raise RuntimeError("Sigma authentication response did not include an access token.")
    return token


def resolve_connection_id(base_url: str, token: str, connection_name: str) -> str:
    """Resolve one exact, active connection name to its Sigma connection ID."""
    if requests is None:
        raise RuntimeError("Missing dependency: install it with 'python -m pip install requests'.")

    matches: list[dict[str, Any]] = []
    page: str | None = None
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        params: dict[str, Any] = {"limit": 1000, "search": connection_name}
        if page:
            params["page"] = page
        response = requests.get(
            f"{base_url}/v2/connections", headers=headers, params=params, timeout=30
        )
        response.raise_for_status()
        data = response.json()
        matches.extend(
            connection
            for connection in data.get("entries", [])
            if connection.get("name") == connection_name and not connection.get("isArchived", False)
        )
        page = data.get("nextPage")
        if not page:
            break

    if not matches:
        raise ValueError(f"No active Sigma connection found with name {connection_name!r}.")
    if len(matches) > 1:
        raise ValueError(
            f"Multiple active Sigma connections are named {connection_name!r}; names must be unique."
        )
    connection_id = matches[0].get("connectionId") or matches[0].get("id")
    if not connection_id:
        raise RuntimeError("Matched connection did not include a connection ID.")
    return str(connection_id)


def get_connection(base_url: str, token: str, connection_id: str) -> dict[str, Any]:
    """Retrieve the current connection state before constructing the PUT payload."""
    if requests is None:
        raise RuntimeError("Missing dependency: install it with 'python -m pip install requests'.")
    response = requests.get(
        f"{base_url}/v2/connections/{connection_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    current = response.json()
    if not isinstance(current, dict):
        raise RuntimeError("Sigma connection response was not a JSON object.")
    return current


def main() -> int:
    args = parse_args()
    try:
        client_id = os.environ.get("SIGMA_CLIENT_ID", "")
        client_secret = os.environ.get("SIGMA_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise ValueError("Set SIGMA_CLIENT_ID and SIGMA_CLIENT_SECRET before running the update.")

        base_url = os.environ.get("SIGMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        token = get_access_token(base_url, client_id, client_secret)
        connection_id = resolve_connection_id(base_url, token, args.connection_name)
        current = get_connection(base_url, token, connection_id)
        payload = build_update_payload(
            current,
            args.catalog,
            args.schema,
            os.environ.get("DATABRICKS_OAUTH_CLIENT_SECRET", ""),
        )
        if args.output_payload:
            try:
                with open(args.output_payload, "x", encoding="utf-8") as output_file:
                    json.dump(payload, output_file, indent=2)
                    output_file.write("\n")
            except FileExistsError as exc:
                raise ValueError(f"Refusing to overwrite existing file {args.output_payload!r}.") from exc
            except OSError as exc:
                raise ValueError(f"Unable to write payload file {args.output_payload!r}: {exc}") from exc
        if args.dry_run:
            print(json.dumps(payload, indent=2))
            print("[DRY RUN] Retrieved the connection; no Sigma API update was submitted.")
            return 0

        response = requests.put(
            f"{base_url}/v2/connections/{connection_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        writebacks = result.get("writebackSchemas") or result.get("writebacks") or []
        print(f"Added a writeback location to {args.connection_name!r} ({connection_id}).")
        print(json.dumps(writebacks, indent=2))
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if requests is not None and isinstance(exc, requests.RequestException):
            print(f"Sigma API request failed: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
