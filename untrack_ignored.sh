#!/bin/bash
# Run this from your repo root. Removes already-tracked files from git's
# index that now match .gitignore, WITHOUT deleting them from disk.
# Your actual working files are untouched -- only git's tracking stops.

set -e

echo "Removing __pycache__ dirs from tracking..."
git rm -r --cached --ignore-unmatch '**/__pycache__' 2>/dev/null || \
  find . -name '__pycache__' -exec git rm -r --cached --ignore-unmatch {} + 2>/dev/null

echo "Removing recent_projects.json from tracking..."
git rm --cached --ignore-unmatch '**/admin_dashboard/config/recent_projects.json'

echo "Removing auth_config.json from tracking (if committed)..."
git rm --cached --ignore-unmatch '**/admin_dashboard/config/auth_config.json'

echo "Removing cv_engine build artifacts from tracking..."
git rm -r --cached --ignore-unmatch cv_engine/build 2>/dev/null || true

echo "Done. Now commit:"
echo "  git add .gitignore"
echo "  git commit -m 'Untrack generated files now covered by .gitignore'"