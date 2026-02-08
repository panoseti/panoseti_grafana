# PANOSETI Grafana Provisioning

This repository manages the "Infrastructure as Code" (IaC) configuration for PANOSETI's Grafana instance. It ensures that our dashboards and datasources are version-controlled, reproducible, and recoverable.

## Workflow Overview

Grafana does not support "Two-Way Sync" natively. We use a **"ClickOps to Git"** workflow:

1.  **Provision:** Grafana launches and loads dashboards from the `dashboards/` directory.
2.  **Edit:** Users edit dashboards in the Grafana UI (enabled via `allowUiUpdates: true`).
3.  **Sync:** An administrator runs `sync_grafana.py` to pull UI changes back to the local `dashboards/` JSON files.
4.  **Commit:** Changes are committed to Git.

## Setup

### 1. Install Dependencies
The sync script requires Python 3 and a few libraries:
```bash
pip install requests python-slugify

```

### 2. Create a Service Account

To allow the script to talk to Grafana, you need a Service Account Token.

1. Log in to Grafana (Admin).
2. Go to **Administration** > **Users and access** > **Service Accounts**.
3. Click **Add service account**.
* **Name:** `SyncBot`
* **Role:** `Viewer` (Safe for pulling) or `Admin` (If pushing is needed later).


4. Click **Add token** -> **Generate token**.
5. Copy the token (e.g., `glsa_...`).

## Usage

### The Sync Script (`sync_grafana.py`)

This script compares the live Grafana dashboards with your local JSON files.

**Basic Usage (Dry Run):**
By default, the script **will not** write to disk. It only shows you what changed.

```bash
export GRAFANA_API_KEY="glsa_YOUR_TOKEN_HERE"
python3 sync_grafana.py

```

**Viewing Diffs:**
To see exactly which lines changed in the JSON:

```bash
python3 sync_grafana.py --diff

```

**Saving Changes (The "Sync"):**
To actually overwrite your local files with the live versions:

```bash
python3 sync_grafana.py --sync

```

### CLI Options

| Flag | Description |
| --- | --- |
| `--key <token>` | Service Account Token (Can also use `GRAFANA_API_KEY` env var) |
| `--url <url>` | Grafana URL (Default: `http://localhost:3000`) |
| `--dir <path>` | Output directory (Default: `./grafana_provisioning/dashboards`) |
| `--sync` | **Required to save.** If omitted, runs in Read-Only mode. |
| `--diff` | Prints colored line-by-line diffs of changes. |

## Directory Structure

* `datasources/`: YAML configurations for InfluxDB, Loki, etc.
* `dashboards/`: JSON models of the dashboards.
* `sync_grafana.py`: The bridge tool.
* `Dockerfile`: Builds the custom Grafana image with these files pre-loaded.


```