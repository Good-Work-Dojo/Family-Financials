#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# Points the sign-in page's "Source code" link at where the running program's source is published, as Settings' About
# card does (deploy/Dockerfile.web runs it on /srv/public/login.html with its FF_SOURCE build argument, so a fork's
# build links to the fork). Only that one attribute changes: the page must hold the repository's link exactly once, and
# the address must be an http(s) address of plain characters, so it can stand in a quoted HTML attribute as it is.
# Usage: sh deploy/source_link.sh PAGE URL      exit 1 when the page or the address is not of that form
set -eu
page=$1
url=$2
default=https://github.com/Good-Work-Dojo/Family-Financials
case "$url" in
  http://?*|https://?*) ;;
  *) echo "FF_SOURCE must be an http or https address, not '$url'" >&2; exit 1 ;;
esac
case "$url" in
  *[!A-Za-z0-9._~:/@%+-]*)
    echo "FF_SOURCE may hold only letters, digits and . _ ~ : / @ % + -, not '$url'" >&2; exit 1 ;;
esac
[ -f "$page" ] || { echo "$page is no file" >&2; exit 1; }
found=$(grep -o "href=\"$default\"" "$page" | wc -l | tr -d ' ')
[ "$found" = 1 ] || { echo "$page links to $default $found times, not once" >&2; exit 1; }
[ "$url" = "$default" ] && exit 0
sed "s|href=\"$default\"|href=\"$url\"|" "$page" >"$page.new"
cat "$page.new" >"$page"
rm "$page.new"
