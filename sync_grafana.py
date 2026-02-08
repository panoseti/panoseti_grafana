#!/usr/bin/env python3
import os
import json
import requests
import argparse
import difflib
import sys
from slugify import slugify

# --- CONFIGURATION ---
DEFAULT_URL = "http://localhost:3000"
DEFAULT_DIR = "./dashboards/panoseti"
IGNORE_FIELDS = ['id', 'version', 'iteration', 'orgId', 'schemaVersion', 'uid']
# Note: removing 'uid' makes it portable, but keeping it helps Grafana link history.
# Usually better to remove 'id' but keep 'uid'.
# I have removed 'uid' from the ignore list below so it stays in the file.
IGNORE_FIELDS = ['id', 'version', 'iteration', 'orgId', 'schemaVersion']


class GrafanaSync:
    def __init__(self, url, key, output_dir, dry_run=False, show_diff=False):
        self.url = url.rstrip('/')
        self.output_dir = output_dir
        self.dry_run = dry_run
        self.show_diff = show_diff
        self.headers = {"Authorization": f"Bearer {key}"}

        if not os.path.exists(self.output_dir) and not self.dry_run:
            os.makedirs(self.output_dir)

    def clean_dashboard(self, dash):
        """Removes noisy fields for cleaner diffs/storage."""
        for field in IGNORE_FIELDS:
            if field in dash:
                del dash[field]
        return dash

    def get_diff(self, old_json, new_json, filename):
        """Generates a git-style diff."""
        old_lines = json.dumps(old_json, indent=2).splitlines()
        new_lines = json.dumps(new_json, indent=2).splitlines()

        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"Local: {filename}",
            tofile=f"Remote: {filename}",
            lineterm=""
        )
        return list(diff)

    def print_diff(self, diff_lines):
        for line in diff_lines:
            if line.startswith('+'):
                print(f"\033[92m{line}\033[0m")  # Green
            elif line.startswith('-'):
                print(f"\033[91m{line}\033[0m")  # Red
            elif line.startswith('^'):
                print(f"\033[94m{line}\033[0m")  # Blue
            else:
                print(line)

    def run(self):
        try:
            # 1. Get Folders
            print(f"Connecting to {self.url}...")
            r = requests.get(f"{self.url}/api/folders", headers=self.headers)
            r.raise_for_status()
            folders = r.json()
            folder_map = {f['id']: f['title'] for f in folders}
            folder_map[0] = "General"  # Default folder

            # 2. Search Dashboards
            r = requests.get(f"{self.url}/api/search?type=dash-db&limit=5000", headers=self.headers)
            r.raise_for_status()
            dashboards = r.json()

            print(f"Found {len(dashboards)} dashboards. Syncing...")

            # 3. Download and Process
            for d in dashboards:
                self.process_dashboard(d, folder_map)

        except requests.exceptions.ConnectionError:
            print(f"\n[ERROR] Could not connect to {self.url}. Is Grafana running?")
            sys.exit(1)
        except requests.exceptions.HTTPError as e:
            print(f"\n[ERROR] HTTP {e.response.status_code}: {e.response.text}")
            sys.exit(1)

    def process_dashboard(self, summary, folder_map):
        uid = summary['uid']

        # Fetch full dashboard model
        r = requests.get(f"{self.url}/api/dashboards/uid/{uid}", headers=self.headers)
        if r.status_code != 200:
            print(f"Error fetching {uid}")
            return

        remote_dash = self.clean_dashboard(r.json()['dashboard'])

        # Determine Path
        folder_id = summary.get('folderId', 0)
        folder_name = slugify(folder_map.get(folder_id, "General"))
        file_name = f"{slugify(remote_dash['title'])}.json"

        folder_path = os.path.join(self.output_dir, folder_name)
        file_path = os.path.join(folder_path, file_name)

        # Read Local (if exists)
        local_dash = {}
        file_exists = os.path.exists(file_path)
        if file_exists:
            with open(file_path, 'r') as f:
                local_dash = json.load(f)

        # Compare
        diff_lines = self.get_diff(local_dash, remote_dash, file_name) if file_exists else ["(New File)"]

        # Logic: If identical, skip. If different, show/save.
        if not diff_lines and file_exists:
            # print(f"  [OK] {folder_name}/{file_name}") # Optional: Uncomment for verbosity
            return

        print(f"\n{'[DRY RUN] ' if self.dry_run else ''}Change detected: {folder_name}/{file_name}")

        if self.show_diff or self.dry_run:
            self.print_diff(diff_lines)

        if not self.dry_run:
            os.makedirs(folder_path, exist_ok=True)
            with open(file_path, 'w') as f:
                json.dump(remote_dash, f, indent=2)
            print(f"  -> Saved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Grafana Dashboards to Local Git Repo")
    parser.add_argument("--key", help="Grafana Service Account Token (or set GRAFANA_API_KEY env var)")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Grafana URL (default: {DEFAULT_URL})")
    parser.add_argument("--dir", default=DEFAULT_DIR, help=f"Output directory (default: {DEFAULT_DIR})")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing files")
    parser.add_argument("--diff", action="store_true", help="Show line-by-line JSON diffs")

    args = parser.parse_args()

    # Priority: Arg > Env Var
    api_key = args.key or os.getenv("GRAFANA_API_KEY")

    if not api_key:
        print("Error: Missing API Key. Provide --key or set GRAFANA_API_KEY environment variable.")
        sys.exit(1)

    syncer = GrafanaSync(args.url, api_key, args.dir, args.dry_run, args.diff)
    syncer.run()