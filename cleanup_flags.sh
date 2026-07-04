#!/bin/bash
# cleanup_flags.sh — helper to remove duplicate flag files and commit the cleanup.
# Run from repository root. This script performs git rm on the listed duplicate paths
# and commits the deletions. Review before running.

set -euo pipefail

DUPLICATES=(
  "pages/flags/gh.svg"
  "pages/flags/ng.svg"
  "pages/flags/ma.svg"
  "pages/flags/ke.svg"
  "pages/flags/ug.svg"
  "gh.svg"
  "ke.svg"
  "ng.svg"
  "ug.svg"
  "ma.svg"
)

echo "This will remove duplicate flag files and commit the changes. Press Enter to continue or Ctrl+C to abort."
read -r

for f in "${DUPLICATES[@]}"; do
  if [ -f "$f" ]; then
    git rm "$f"
    echo "Removed $f"
  else
    echo "Not found: $f"
  fi
done

git commit -m "chore(flags): remove duplicate flag files (cleanup)"

echo "Cleanup committed. Push the branch and open a PR to merge."
