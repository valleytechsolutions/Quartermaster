#!/usr/bin/env bash
# Replace assets on a GitHub release so a published name never goes missing or half uploaded.
#
#   tools/replace_release_assets.sh <tag> <file>...
#
# Every file is first uploaded as <name>.next while the current assets stay live. Only when all uploads have
# succeeded is each old asset removed and its .next copy renamed into place, in the order given, so list the
# files that point at others (like an Imager list) last. Needs gh with GH_REPO and GH_TOKEN set.
set -euo pipefail

[ $# -ge 2 ] || { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
tag=$1
shift
: "${GH_REPO:?set GH_REPO to owner/name}"

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

release=$(gh api "repos/$GH_REPO/releases/tags/$tag" --jq .id)
[[ $release =~ ^[0-9]+$ ]] || { echo "replace_release_assets: no release $tag" >&2; exit 1; }

asset_id() {
    gh api --paginate "repos/$GH_REPO/releases/$release/assets" --jq ".[] | select(.name == \"$1\") | .id"
}

names=()
for file in "$@"; do
    name=$(basename "$file")
    [[ $name =~ ^[A-Za-z0-9._-]+$ ]] || { echo "replace_release_assets: unexpected name $name" >&2; exit 1; }
    [ -f "$file" ] || { echo "replace_release_assets: no file $file" >&2; exit 1; }
    names+=("$name")
done

for file in "$@"; do
    name=$(basename "$file")
    for stale in $(asset_id "$name.next"); do
        gh api -X DELETE "repos/$GH_REPO/releases/assets/$stale"
    done
    ln -s "$(cd "$(dirname "$file")" && pwd)/$name" "$stage/$name.next"
    gh release upload "$tag" "$stage/$name.next"
done

for name in "${names[@]}"; do
    next=$(asset_id "$name.next")
    [[ $next =~ ^[0-9]+$ ]] || { echo "replace_release_assets: $name.next did not arrive" >&2; exit 1; }
    for old in $(asset_id "$name"); do
        gh api -X DELETE "repos/$GH_REPO/releases/assets/$old"
    done
    gh api -X PATCH "repos/$GH_REPO/releases/assets/$next" -f name="$name" --silent
    echo "replaced $name"
done
