#!/usr/bin/env bash
set -euo pipefail

# Args: $1=current_pr_number $2=pr_order_file
CURRENT="$1"
PR_ORDER_FILE="$2"

NEXT=$(jq -r --arg cur "$CURRENT" '
    (to_entries | map(select(.value.number == ($cur | tonumber))) | .[0].key) as $idx |
    if ($idx + 1) < length then .[$idx + 1].number | tostring
    else "done"
    end
' "$PR_ORDER_FILE")

echo "$NEXT"
