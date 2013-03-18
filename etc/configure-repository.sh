#!/bin/bash
#
# Configures the local Git repository.
#
# Normally this script should be run immediately after cloning a new
# repository. Occasionally it may need to be run subsequently after
# adding or changing Git hooks, aliases, etc.
#
# Note: This script must be idempotent; running it multiple times must
# result in a properly configured repository.
#
# Author: jacob@nextdoor.com (Jacob Hesch)

root=$(git rev-parse --show-toplevel)
if [[ $(basename $root) != 'zkwatcher' ]]; then
    echo "Error: $0 must be run from inside the zkwatcher repo"
    exit 1
fi

# Create symlinks under .git/hooks to hook scripts in the githooks
# directory.
hooks="
    commit-msg
"

git_hooks_dir=$(git rev-parse --git-dir)/hooks
target_dir=../../etc/githooks
for hook in $hooks; do
    ln -sf $target_dir/$hook $git_hooks_dir/$hook
done

# Configure remote tracking branches for rebase.
git config branch.master.rebase true
git config branch.autosetuprebase remote

# Configure git change to use our review host
git config git-change.gerrit-ssh-host review.opensource.nextdoor.com
