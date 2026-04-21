#!/usr/bin/env bash
# Squash the currently-staged refactor/docker-flag-to-subcommand changes
# into 2 commits using `git read-tree` to set the split point cleanly.
#
# Preconditions (set up by your earlier `git reset --soft <branch-base>`):
#   - On branch refactor/docker-flag-to-subcommand
#   - HEAD == dev tip (or wherever the soft-reset put you)
#   - All 9 commits' worth of changes are staged
#   - origin/refactor/docker-flag-to-subcommand still points at the original tip
#
# Result: 2 commits on top of HEAD, final tree identical to origin.

set -euo pipefail

BRANCH="refactor/docker-flag-to-subcommand"
ORIGIN_REF="origin/$BRANCH"
SPLIT="a9ce04a"   # tree boundary: group 1 ends here

MSG1='[zutils] E: Move Docker flags to subcommands

Replace the deprecated --docker / --no-docker top-level flags with
explicit docker / no-docker subcommands. Update README, examples, and
tests; remove the deprecation shim and tighten edge-case handling
around the new subcommand parser.

Squashed from 53c4be1, 542f746, 3f95621, 0177605, a9ce04a.'

MSG2='[zutils] E: Expose DOCKER_SUBCOMMANDS override

Allow the docker subcommand list to be overridden via the
DOCKER_SUBCOMMANDS environment variable, with tests covering the
override behaviour. Includes follow-up review fixes and a help-parser
type-error fix.

Squashed from e8cf8db, 6ad2202, aabd9f1, 8079f62.'

# --- Sanity ---
cur="$(git rev-parse --abbrev-ref HEAD)"
[[ "$cur" == "$BRANCH" ]] || { echo "ERROR: on $cur, expected $BRANCH" >&2; exit 1; }

git rev-parse --verify "$ORIGIN_REF" >/dev/null || {
  echo "ERROR: $ORIGIN_REF not found" >&2; exit 1; }
git rev-parse --verify "$SPLIT" >/dev/null || {
  echo "ERROR: split commit $SPLIT not found" >&2; exit 1; }

ORIG_TIP="$(git rev-parse "$ORIGIN_REF")"
ORIG_TREE="$(git rev-parse "$ORIG_TIP^{tree}")"
WT_BASE="$(git rev-parse HEAD)"
echo "Branch:      $BRANCH"
echo "Current HEAD (post-soft-reset): $WT_BASE"
echo "Origin tip:  $ORIG_TIP"
echo "Split point: $SPLIT"
echo

# Verify the staged + working-tree contents match the origin tip's tree
# (i.e. soft reset really preserved everything).
WT_INDEX_TREE="$(git write-tree)"
if [[ "$WT_INDEX_TREE" != "$ORIG_TREE" ]]; then
  echo "WARN: staged index tree ($WT_INDEX_TREE) != origin tree ($ORIG_TREE)"
  echo "      Continuing, but the final verification may fail."
  echo
fi

# --- Commit 1: tree = $SPLIT ---
echo "Setting index to tree of $SPLIT..."
git read-tree "$SPLIT"
echo "Creating commit 1..."
git commit -m "$MSG1"
C1="$(git rev-parse HEAD)"
echo "  -> $C1"
echo

# --- Commit 2: stage remaining diff (working tree still has full origin contents) ---
echo "Staging remaining changes..."
git add -A
echo "Creating commit 2..."
git commit -m "$MSG2"
C2="$(git rev-parse HEAD)"
echo "  -> $C2"
echo

# --- Verify ---
NEW_TREE="$(git rev-parse HEAD^{tree})"
echo "Final tree: $NEW_TREE"
echo "Origin tree: $ORIG_TREE"
if [[ "$NEW_TREE" == "$ORIG_TREE" ]]; then
  echo "OK: tree matches origin exactly."
else
  echo "ERROR: tree mismatch! Diff:" >&2
  git --no-pager diff HEAD "$ORIGIN_REF" | head -50 >&2
  echo "Rollback: git reset --hard $ORIGIN_REF" >&2
  exit 1
fi

echo
git --no-pager log --oneline "$WT_BASE..HEAD"
echo
echo "Done. Next steps when ready:"
echo "  Push:     git push --force-with-lease=$BRANCH:$ORIG_TIP"
echo "  Rollback: git reset --hard $ORIG_TIP"
