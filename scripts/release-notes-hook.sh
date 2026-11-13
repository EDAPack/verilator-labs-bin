#!/usr/bin/env bash
# Release-notes hook, run by edapack-common's build-release.yml in the publish
# job (step "Tool-repo release-notes hook"), from this repo's checkout at the
# dispatched ref, which is the release tag.
#
# Contract: scripts/release-notes-hook.sh <artifacts-dir> <upload-dir>,
# Markdown on stdout, appended to the release body. Soft-fails there. The
# arguments are unused: the notes come from releases/<base-tag>.yaml in this
# checkout (a re-spin tag v<ver>-labs.<YYYY>.<NN>.<n> uses its base record).
# Env from the workflow: EC_TAG (the release tag), EC_PACKAGE, EC_VERSION, ...
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
: "${EC_TAG:?EC_TAG unset (set by build-release.yml)}"

# labs.py reads the record with PyYAML.
if ! python3 -c 'import yaml' 2>/dev/null; then
    python3 -m pip install -q pyyaml >&2 \
        || python3 -m pip install -q --break-system-packages pyyaml >&2
fi

exec python3 "$root/scripts/labs.py" --root "$root" release-notes "$EC_TAG"
