- **Docker hub with a new .env:** `docker compose up` no longer stops with "required variable CHRONICLE_DOMAIN is
  missing a value" on recent Docker Compose when `.env` uses the `INTERLATCH_*` names. A missing domain or admin email
  is now explained in `docker compose logs hub` ([Docker](docs/docker.md)).
