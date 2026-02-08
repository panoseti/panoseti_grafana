#!/usr/bin/env python3
import os
import json
import requests
import argparse
import difflib
import sys
from slugify import slugify  # pip install python-slugify

# --- CONFIGURATION ---
DEFAULT_URL = "http://localhost:3000"
DEFAULT_DIR = "./dashboards"
IGNORE_FIELDS = ['id', 'version', 'iteration', 'orgId', 'schemaVersion', 'from', 'to', 'updated']


class GrafanaSync:
    def __init__(self, url, key, output_dir, dry_run=False, show_diff=False):
        self.url = url.rstrip('/')
        self.output_dir = output_dir
        self.dry_run = dry_run
        self.show_diff = show_diff
        self.headers = {"Authorization": f"Bearer {key}"}
        self.local_file_map = self._index_local_files()

    def _index_local_files(self):
        """Map UIDs to existing file paths."""
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
                    except:
                        pass
        return file_map

    def clean_dashboard(self, dash):
        # 1. Remove Noisy Metadata (Standard)
        for field in IGNORE_FIELDS:
            if field in dash:
                del dash[field]

        # 2. Enforce defaults
        # Even if you were zoomed in to a 5-minute spike when you saved,
        # the file on disk will always reset to the standard view.
        dash['time'] = {"from": "now-30m", "to": "now"}
        dash['refresh'] = "5s"

        # 3. Neutralize variables (Multi-Site Fix)
        # This prevents "UCB_lab" from being saved in the file.
        # When loaded at Palomar, Grafana will run the query and pick the local observatory.
        if 'templating' in dash and 'list' in dash['templating']:
            for var in dash['templating']['list']:
                # Only clean 'query' variables (like Observatory)
                if var.get('type') == 'query':

                    # Delete 'current' selection so it defaults to the first available result
                    if 'current' in var:
                        del var['current']

                    # Clear cached 'options' to keep the JSON file small and clean
                    if 'options' in var:
                        var['options'] = []

        return dash

    def get_diff(self, old, new, filename):
        return list(difflib.unified_diff(
            json.dumps(old, indent=2).splitlines(),
            json.dumps(new, indent=2).splitlines(),
            fromfile="Local", tofile="Remote", lineterm=""
        ))

    def run(self):
        try:
            # 1. Get Folder Names
            r = requests.get(f"{self.url}/api/folders", headers=self.headers)
            r.raise_for_status()
            folders = {f['id']: f['title'] for f in r.json()}
            folders[0] = "General"

            # 2. Get Dashboards
            r = requests.get(f"{self.url}/api/search?type=dash-db&limit=5000", headers=self.headers)
            r.raise_for_status()

            print(f"Syncing {len(r.json())} dashboards...")
            for d in r.json():
                self.process_dashboard(d, folders)

        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)

    def process_dashboard(self, summary, folders):
        uid = summary['uid']
        title = summary['title']

        # 1. Fetch Remote
        r = requests.get(f"{self.url}/api/dashboards/uid/{uid}", headers=self.headers)
        if r.status_code != 200: return
        remote_dash = self.clean_dashboard(r.json()['dashboard'])

        # 2. Determine Ideal Path (Sluggified)
        folder_name = slugify(folders.get(summary.get('folderId', 0), "General"))
        ideal_filename = f"{slugify(title)}.json"
        ideal_path = os.path.join(self.output_dir, folder_name, ideal_filename)

        current_path = self.local_file_map.get(uid)
        is_new = current_path is None
        is_rename = current_path and (os.path.abspath(current_path) != os.path.abspath(ideal_path))

        # 3. Read Local Content
        local_dash = {}
        if current_path and os.path.exists(current_path):
            with open(current_path, 'r') as f:
                local_dash = self.clean_dashboard(json.load(f))

        # 4. Check for Content Changes
        diff = self.get_diff(local_dash, remote_dash, ideal_filename)

        if not diff and not is_rename:
            return  # Nothing to do

        # 5. Execute Changes
        if is_rename:
            print(f"\n[RENAME] {current_path} -> {ideal_path}")
            if not self.dry_run:
                if os.path.exists(current_path):
                    os.remove(current_path)  # Delete old file

        if diff:
            action = "[NEW]" if is_new else "[MODIFIED]"
            print(f"\n{action} {ideal_path}")
            if self.show_diff or self.dry_run:
                for line in diff:
                    if line.startswith('---') or line.startswith('+++'): continue
                    color = "\033[92m" if line.startswith('+') else "\033[91m" if line.startswith('-') else "\033[0m"
                    print(f"{color}{line}\033[0m")

        if not self.dry_run:
            os.makedirs(os.path.dirname(ideal_path), exist_ok=True)
            with open(ideal_path, 'w') as f:
                json.dump(remote_dash, f, indent=2)
            print("  -> Saved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", help="Grafana Service Account Token")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--dir", default=DEFAULT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--diff", action="store_true")
    args = parser.parse_args()

    key = args.key or os.getenv("GRAFANA_API_KEY")
    if not key:
        print("Set GRAFANA_API_KEY env var.")
        sys.exit(1)

    GrafanaSync(args.url, key, args.dir, args.dry_run, args.diff).run()