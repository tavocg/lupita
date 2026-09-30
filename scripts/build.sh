#!/bin/sh
# First argument: output directory. Remaining arguments are passed to Hugo.
set -eu
destination=${1:-public}
if [ "$#" -gt 0 ]; then shift; fi
hugo --minify --destination "$destination" "$@"
npx --yes pagefind@1.5.2 --site "$destination"
