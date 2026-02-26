#!/bin/bash
NEW_VERSION=$1
if [ -z "$NEW_VERSION" ]; then
  echo "Usage: ./bump_version.sh 2026.02.4"
  exit 1
fi

# Update web/VERSION
echo "$NEW_VERSION" > web/VERSION

# Update pyproject.toml
sed -i '' -e "s/^version = \".*\"/version = \"$NEW_VERSION\"/" web/pyproject.toml

# Update config_server.py
sed -i '' -e "s/^__version__ = \".*\"/__version__ = \"$NEW_VERSION\"/" web/config_server.py

echo "Bumped all files to $NEW_VERSION"
