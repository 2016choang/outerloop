#!/usr/bin/env bash
# Builds dist-docs/ from docs/ (mkdocs.yml) with the docs dependency group
# only, never the package itself. Staging by default: noindex pages plus a
# robots.txt that blocks crawlers; OUTERLOOP_DOCS_STAGING=false turns both
# off for the public build.
set -euo pipefail

cd "$(dirname "$0")/../.."

rm -rf dist-docs
uv run --only-group docs mkdocs build --strict -d dist-docs
