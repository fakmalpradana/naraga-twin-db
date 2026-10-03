#!/usr/bin/env bash
# Prints random values for the secrets Railway needs. Copy them into Railway Variables; do not commit them.
set -euo pipefail
gen() { openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-32; }
for k in POSTGRES_PASSWORD APP_DB_PASSWORD API_DB_PASSWORD ADMIN_PASSWORD; do echo "$k=$(gen)"; done
echo "SECRET_KEY=$(openssl rand -base64 60 | tr -d '\n')"
