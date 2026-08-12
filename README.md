# Sigma Writeback Connection Updater

This repository contains a small utility for changing where a Sigma connection writes data. It updates the connection's **writeback catalog/database and schema** through the Sigma API.

The utility does not create a connection or change how Sigma reads data. It resolves the connection by name, retrieves its current state, converts the retrieved values into a connection update payload, appends a new location, and sends that payload to Sigma. Sigma's update endpoint uses `PUT`.

The existing Sigma connection remains the same connection. The operation adds a **new writeback destination** defined by `WRITEBACK_CATALOG` and `WRITEBACK_SCHEMA`; it does not replace an existing writeback destination. All existing entries remain in `writebackSchemas` ahead of the newly appended entry.

## What it changes

The automatic payload builder currently supports Databricks connections using either OAuth or non-OAuth authentication:

- For an OAuth connection (`details.useOauth` is `true`), it appends to `details.writebackSchemas`.
- For a non-OAuth connection, it appends to `details.writeAccess`.
- The destination uses `writeCatalog` and `writeSchema`.

Connection fields are sourced from Sigma's `GET /v2/connections/{connectionId}` response. For connection-level OAuth, the OAuth client secret is the only required supplemental value because Sigma does not return secrets.

## Files

- `connection-writeback/update-connection-writeback.py` — command-line utility.
- `connection-writeback/update-connection-writeback.ipynb` — notebook version of the workflow.
- `connection-writeback/connection.json.example` — example of a generated Databricks OAuth payload.

## Use the Databricks notebook

The notebook is intended to run inside a Databricks workspace and add a writeback location to a Sigma Databricks OAuth connection.

### 1. Import and attach the notebook

Import `connection-writeback/update-connection-writeback.ipynb` into your Databricks workspace and attach it to running compute with network access to the Sigma API. The notebook uses `dbutils`, so it is not designed to run as a local Jupyter notebook without modification.

The compute environment must have the `requests` Python package. It is normally available in Databricks runtimes; if it is missing, install it on the compute before continuing.

### 2. Store credentials in a Databricks secret scope

Create or use a Databricks secret scope and add these three secrets:

| Default secret key | Value |
| --- | --- |
| `sigma_client_id` | Sigma API client ID |
| `sigma_client_secret` | Sigma API client secret |
| `databricks_oauth_client_secret` | OAuth client secret used by the Sigma connection to access Databricks |

The default scope name is `sigma`. You can use a different scope or different key names by changing the corresponding notebook widgets.

The Sigma API client must be able to list and update connections. The Databricks OAuth credentials must have the access required by the existing connection and its writeback destination.

### 3. Generate the payload from the connection

No connection payload needs to be entered manually. After resolving the connection, the notebook retrieves its host, HTTP path, OAuth configuration, user attributes, connection options, and current writeback destinations. It converts those retrieved fields into the structure required by Sigma's update endpoint and adds the requested writeback destination.

If the Databricks HTTP path is configured with **Use User Attribute**, the retrieved connection includes a `userAttributes.warehouse` mapping. The notebook copies that mapping into `details.userAttributes` in the update payload so the HTTP-path user attribute remains enabled. Confirm it appears in the dry-run output, for example:

```json
{
  "details": {
    "userAttributes": {
      "warehouse": "HTTP_PATH_USER_ATTRIBUTE"
    }
  }
}
```

### 4. Set the notebook widgets

Run the widget cell, then provide values at the top of the notebook:

| Widget | Required value |
| --- | --- |
| `connection_name` | Exact name of one active Sigma connection |
| `catalog` | New Databricks writeback catalog |
| `schema` | New Databricks writeback schema |
| `secret_scope` | Scope containing the three secrets; defaults to `sigma` |
| `client_id_secret_key` | Sigma client ID key; defaults to `sigma_client_id` |
| `client_secret_secret_key` | Sigma client secret key; defaults to `sigma_client_secret` |
| `databricks_oauth_client_secret_secret_key` | Databricks OAuth client secret key |
| `sigma_base_url` | Sigma API host; defaults to the AWS Sigma API |
| `dry_run` | Keep this set to `true` for the first run |

### 5. Run and review the dry run

Run all cells with `dry_run` set to `true`. The notebook authenticates, resolves the connection, and retrieves its current state, but it does not submit the `PUT` update. Confirm that:

- The connection name is correct.
- `writeCatalog` and `writeSchema` contain the intended destination.
- The host, HTTP path, OAuth configuration, user attributes, and existing writebacks match the retrieved connection.

The printed payload can contain sensitive connection configuration. Treat notebook output as confidential, restrict notebook permissions, and clear the output before sharing or exporting the notebook.

### 6. Apply the update

Change `dry_run` to `false` and rerun the notebook. It authenticates to Sigma, finds one active connection with an exact name match, and submits the updated payload. A successful run prints an `[OK]` message with the connection ID.

The notebook stops without updating anything if the connection name has no exact active match or matches multiple active connections. After a successful update, verify the connection and writeback destination in Sigma before relying on it for production writes.

The notebook rejects the request if the same catalog and schema already exist. Otherwise, it preserves all retrieved writeback locations and appends the new one.

## Requirements

- Python 3.9 or newer
- The `requests` Python package
- A Sigma API client ID and secret with permission to list and update connections
- The Databricks OAuth client secret when the connection uses connection-level OAuth

Install the Python dependency:

```bash
python -m pip install requests
```

## Supply the non-retrievable OAuth secret

For a connection-level OAuth connection, provide the Databricks OAuth client secret in the environment. It is intentionally absent from Sigma's GET response:

```bash
export DATABRICKS_OAUTH_CLIENT_SECRET="your-databricks-oauth-client-secret"
```

Organization-level OAuth does not require this value.

## Preview the change

Use a dry run first. It authenticates to Sigma and retrieves the current connection, then prints the proposed payload without submitting the update:

```bash
python connection-writeback/update-connection-writeback.py \
  --connection-name "My Sigma Connection" \
  --catalog "TARGET_CATALOG" \
  --schema "TARGET_SCHEMA" \
  --dry-run
```

Automatic payload construction rejects non-Databricks connections rather than risk building an incomplete update payload.

## Apply the change

Set the Sigma API credentials in the environment, then run the command without `--dry-run`:

```bash
export SIGMA_CLIENT_ID="your-client-id"
export SIGMA_CLIENT_SECRET="your-client-secret"

python connection-writeback/update-connection-writeback.py \
  --connection-name "My Sigma Connection" \
  --catalog "TARGET_CATALOG" \
  --schema "TARGET_SCHEMA"
```

The script:

1. Authenticates with Sigma using client credentials.
2. Finds one active connection whose name exactly matches `--connection-name`.
3. Retrieves the current connection with `GET /v2/connections/{connectionId}`.
4. Builds the update payload from the retrieved connection and appends the new location.
5. Sends the complete payload with `PUT /v2/connections/{connectionId}`.

The command stops if no exact active match is found or if multiple active connections share the same name.

## Save the generated connection payload

Use `--output-payload` to save the payload constructed from the retrieved connection before updating it:

```bash
python connection-writeback/update-connection-writeback.py \
  --connection-name "My Sigma Connection" \
  --catalog "TARGET_CATALOG" \
  --schema "TARGET_SCHEMA" \
  --output-payload connection.json \
  --dry-run
```

For safety, the command refuses to overwrite an existing output file. The generated file contains the OAuth secret for connection-level OAuth, so do not commit or share it.

## How existing locations are preserved

Sigma's connection-details response represents writeback locations differently from the connection update request. The project converts each retrieved `database`/`schema` pair to the Databricks request form (`writeCatalog`/`writeSchema`), retains its description when present, and appends the requested location. It refuses to add an exact duplicate.

The GET response cannot be sent directly to the update endpoint because it uses a different shape. The utility maps response fields into `details`, converts writeback entries to the request format, and supplies the non-retrievable OAuth client secret separately. When the retrieved connection contains `userAttributes`, the utility preserves that object in `details.userAttributes`; for Databricks, its `warehouse` entry retains **Use User Attribute** for the HTTP path.

## Optional API base URL

The default API host is `https://aws-api.sigmacomputing.com`. To use another Sigma deployment, set `SIGMA_BASE_URL`:

```bash
export SIGMA_BASE_URL="https://your-sigma-api-host"
```

## Important notes

- Back up or otherwise retain the current connection configuration before applying an update.
- Verify the generated payload with `--dry-run` before sending it.
- The script changes connection configuration only; it does not move existing data or create the target catalog, database, or schema.
- The target location and connection credentials must already have the permissions required for Sigma writeback operations.
