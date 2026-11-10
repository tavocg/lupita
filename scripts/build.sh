#!/bin/sh
# First argument: output directory. Remaining arguments are passed to Hugo.
set -eu
destination=${1:-public}
if [ "$#" -gt 0 ]; then shift; fi
command -v rsvg-convert >/dev/null 2>&1 || {
  echo "Se requiere rsvg-convert (librsvg2-bin) para generar la portada social." >&2
  exit 1
}
hugo --minify --destination "$destination" "$@"
rsvg-convert "$destination/social-card.svg" --output "$destination/social-card.png"
npx --yes pagefind@1.5.2 --site "$destination"
