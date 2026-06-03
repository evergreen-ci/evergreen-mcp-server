#!/bin/bash
# Prints the current Kanopy access token from the token file.
# Usage: export KANOPY_TOKEN=$(./scripts/kanopy-token.sh)
TOKEN_FILE="$HOME/.kanopy/token-oidclogin.json"
python3 -c "import json; print(json.load(open('$TOKEN_FILE'))['access_token'])" 2>/dev/null
