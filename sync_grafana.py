#!/usr/bin/env python3
"""
Grafana Dashboard Sync Tool
---------------------------
Synchronizes dashboards from a live Grafana instance back to local JSON files
for Git version control.

Key Features:
1.  **Sanitization**: Strips 'id', 'version', and environment-specific variables
    to prevent Git noise and configuration drift.
2.  **Enforcement**: Resets dashboard time ranges and refresh rates to defaults.
3.  **Smart Sync**: Updates files in-place by matching UIDs, handling renames
    and directory moves automatically.

Usage:
    # Dry Run (Default)
    python3 sync_grafana.py

    # Save Changes (Overwrite local files)
    python3 sync_grafana.py --sync
"""

import os
import json
import requests
import argparse
import difflib
import sys
from typing import Dict, List, Any, Optional
from slugify import slugify  # pip install python-slugify

# --- CONFIGURATION ---
DEFAULT_URL = "http://localhost:3000"
DEFAULT_DIR = "./grafana_provisioning/dashboards"

# Fields to REMOVE from the JSON to keep it clean in Git
IGNORE_FIELDS = [
    'id', 'version', 'iteration', 'orgId', 'schemaVersion',
    'from', 'to', 'updated'
]


class GrafanaSync:
    """
    Handles the synchronization logic between Grafana API and local filesystem.
    """

    def __init__(self, url: str, key: str, output_dir: str, sync: bool = False, show_diff: bool = False):
        """
        Initialize the sync engine.

        :param url: Base URL of Grafana (e.g. http://localhost:3000)
        :param key: Service Account Token / API Key
        :param output_dir: Local directory to store JSON files
        :param sync: If True, write changes to disk. If False, dry-run only.
        :param show_diff: If True, print line-by-line diffs to stdout.
        """
        self.url = url.rstrip('/')
        self.output_dir = output_dir
        self.sync = sync
        self.show_diff = show_diff
        self.headers = {"Authorization": f"Bearer {key}"}

        # Build map of {uid: filepath} to handle file moves/renames
        self.local_file_map = self._index_local_files()
        self.changes_detected = False

    def _index_local_files(self) -> Dict[str, str]:
        """
        Scans the output directory to map Dashboard UIDs to existing file paths.
        This allows us to update files in-place even if they are moved/renamed.
        """
        file_map = {}
        if not os.path.exists(self.output_dir):
            return file_map
        for root, _, files in os.walk(self.output_dir):
            for file in files:
                if file.endswith(".json"):
                    path = os.path.join(root, file)
                    try:
                        with open(path, 'r') as f:
                            data = json.load(f)
                            if 'uid' in data:
                                file_map[data['uid']] = path
                    except Exception:
                        pass
        return file_map

    def clean_dashboard(self, dash: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitizes the dashboard JSON to reduce noise in Git diffs and enforce policies.
        This is applied to the REMOTE dashboard before saving.
        """
        # 1. Remove Noisy Metadata (Standard)
        for field in IGNORE_FIELDS:
            if field in dash:
                del dash[field]

        # 2. ENFORCE DEFAULTS (Policy: Always reset time range on save)
        # This ensures dashboards always open in "Live Monitoring" mode
        dash['time'] = {"from": "now-30m", "to": "now"}
        dash['refresh'] = "5s"

        # 3. NEUTRALIZE VARIABLES (Policy: Remove hardcoded environment selections)
        # This prevents "UCB_lab" from being saved in the file, forcing Grafana
        # to re-evaluate the query when loaded at a different site (e.g. Palomar).
        if 'templating' in dash and 'list' in dash['templating']:
            for var in dash['templating']['list']:
                if var.get('type') == 'query':
                    # Delete 'current' selection so it defaults to the first available result
                    if 'current' in var:
                        del var['current']
                    # Clear cached 'options' to keep the JSON file small
                    if 'options' in var:
                        var['options'] = []

        return dash

    def get_diff(self, old: Dict[str, Any], new: Dict[str, Any], filename: str) -> List[str]:
        """Generates a unified diff list between two JSON objects."""
        old_lines = json.dumps(old, indent=2).splitlines()
        new_lines = json.dumps(new, indent=2).splitlines()

        return list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile="Local (Disk)", tofile="Remote (Cleaned)", lineterm=""
        ))

    def run(self) -> None:
        """Main execution loop."""
        try:
            # 1. Get Folder Names
            print(f"Connecting to {self.url}...")
            r = requests.get(f"{self.url}/api/folders", headers=self.headers)
            r.raise_for_status()
            folders = {f['id']: f['title'] for f in r.json()}
            folders[0] = "General"

            # 2. Get Dashboards
            r = requests.get(f"{self.url}/api/search?type=dash-db&limit=5000", headers=self.headers)
            r.raise_for_status()

            dashboards = r.json()
            print(f"Found {len(dashboards)} dashboards. Processing...")

            for d in dashboards:
                self.process_dashboard(d, folders)

            # This ensures the user sees the status even if the log was long.
            print("\n" + "=" * 60)
            if self.changes_detected:
                if not self.sync:
                    print("\033[93m[WARN] DRY RUN COMPLETE. Changes detected but NOT saved.\033[0m")
                    print("       Run with \033[1m--sync\033[0m to overwrite local files.")
                else:
                    print("\033[92m[SUCCESS] Sync complete. Local files updated.\033[0m")
            else:
                print("\033[92m[OK] No changes detected. Local files match Remote.\033[0m")
            print("=" * 60)

        except Exception as e:
            print(f"\033[91m[ERROR] {e}\033[0m")
            sys.exit(1)

    def process_dashboard(self, summary: Dict[str, Any], folders: Dict[int, str]) -> None:
        uid = summary['uid']
        title = summary['title']

        # 1. Fetch Remote
        r = requests.get(f"{self.url}/api/dashboards/uid/{uid}", headers=self.headers)
        if r.status_code != 200:
            return

        # Clean the REMOTE dashboard (This is what we WANT the file to look like)
        remote_dash = self.clean_dashboard(r.json()['dashboard'])

        # 2. Determine Ideal Path (Sluggified)
        folder_name = slugify(folders.get(summary.get('folderId', 0), "General"))
        ideal_filename = f"{slugify(title)}.json"
        ideal_path = os.path.join(self.output_dir, folder_name, ideal_filename)

        current_path = self.local_file_map.get(uid)

        # Check if file exists or is new
        is_new = current_path is None

        # Check if file needs renaming (only if we have a current path)
        is_rename = current_path and (os.path.abspath(current_path) != os.path.abspath(ideal_path))

        # 3. Read Local Content
        local_dash = {}
        if current_path and os.path.exists(current_path):
            with open(current_path, 'r') as f:
                # We want to compare the "Dirty" local file against the "Clean" remote file.
                # This ensures that if the local file has 'id' or 'version', it triggers a diff.
                local_dash = json.load(f)

        # 4. Check for Content Changes
        diff = self.get_diff(local_dash, remote_dash, ideal_filename)

        if not diff and not is_rename:
            return  # Exact Match

        self.changes_detected = True

        # 5. Execute Changes
        if is_rename:
            print(f"\n\033[96m[RENAME]\033[0m {current_path} -> {ideal_path}")
            if self.sync:
                if os.path.exists(current_path):
                    os.remove(current_path)

        if diff:
            action = "\033[92m[NEW]\033[0m" if is_new else "\033[93m[MODIFIED]\033[0m"
            print(f"\n{action} {ideal_path}")

            # Show diff if requested OR if we are in dry-run mode (implicit diff)
            if self.show_diff or not self.sync:
                for line in diff:
                    if line.startswith('---') or line.startswith('+++'): continue
                    color = "\033[92m" if line.startswith('+') else "\033[91m" if line.startswith('-') else "\033[0m"
                    print(f"{color}{line}\033[0m")

        if self.sync:
            os.makedirs(os.path.dirname(ideal_path), exist_ok=True)
            with open(ideal_path, 'w') as f:
                json.dump(remote_dash, f, indent=2)
            print("  -> Saved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sync Grafana Dashboards to Local Git Repo",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--key", help="Grafana Service Account Token (or set GRAFANA_API_KEY env var)")
    parser.add_argument("--url", default=DEFAULT_URL, help="Grafana URL")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="Output directory for JSON files")

    parser.add_argument("--sync", action="store_true", help="Write changes to disk (Enable Save Mode)")

    parser.add_argument("--diff", action="store_true", help="Show line-by-line JSON diffs")

    args = parser.parse_args()

    key = args.key or os.getenv("GRAFANA_API_KEY")
    if not key:
        print("\033[91mError: Missing API Key. Provide --key or set GRAFANA_API_KEY environment variable.\033[0m")
        sys.exit(1)

    GrafanaSync(args.url, key, args.dir, args.sync, args.diff).run()