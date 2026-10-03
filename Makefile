# One-command bootstrap. Needs Docker; copy .env.example to .env first.
.PHONY: up down logs test psql build-docs
up: .env ; docker compose up -d --build
down: ; docker compose down
logs: ; docker compose logs -f api
.env: ; cp .env.example .env && echo "created .env - edit passwords"
psql: ; docker compose exec db psql -U postgres
test: ; docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -q -f - < db/tests/invariants.sql \
  && docker compose exec -T api python -m pytest -q tests
build-docs: ; python3 docs/en/build.py
# import: ./scripts/import.sh USER=<name> ... (added with the import wrapper)
