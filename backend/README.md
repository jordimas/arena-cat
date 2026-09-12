# Arena Cat backend

PostgreSQL server, data model (SQLAlchemy) and migrations (Alembic) for Arena Cat.

## Requirements

- [Docker](https://www.docker.com/) and Docker Compose
- [uv](https://docs.astral.sh/uv/)

## Getting started

From the repository root:

```bash
make setup  # create the local database and apply migrations
```

Optionally run `cd backend && uv run pre-commit install` to install the lint/format git hook.

## Structure

```text
app/
  config.py     # connection settings (.env)
  db.py         # SQLAlchemy engine and base class
  models.py     # data models
  schemas.py    # Pydantic models for API validation
  routes/       # FastAPI endpoints (task, vote, ranking)
  services/     # logic and database operations
  ranking/      # ranking module
migrations/     # Alembic migrations
tests/          # tests
```

## Data model

ER diagram of the schema: [docs/db_schema.md](../docs/db_schema.md).

## Users and authentication

Detailed documentation of user management and authentication (data model, cryptography,
auth flows, endpoints and GDPR): [docs/usuaris_autenticacio.md](../docs/usuaris_autenticacio.md).

## Databases and roles

On first startup, `docker compose` provisions:

- **arena_cat** — application database.
- **arena_cat_test** — test database, derived from `POSTGRES_DB` (`${POSTGRES_DB}_test`).
- **arena_app** — application role with limited permissions (DML only). Migrations run
  with the superuser.

## API

El backend de FastAPI exposa els següents endpoints:

### `GET /api/categories`

Retorna el catàleg públic de categories, ordenat per codi. Les dades provenen de la taula
`categories`, sincronitzada des de `data/prompts/categories.yaml`.

```json
{
  "categories": [
    {
      "code": "correccio",
      "name": "Correcció",
      "description": "Corregeix aquest text.",
      "evaluation_instructions": "- Correcció ortogràfica i gramatical.\n- Conservació del significat original.\n- Naturalitat en català.\n- Absència de canvis innecessaris."
    }
  ]
}
```

### `GET /api/task`

Obté una nova tasca (un prompt amb dues respostes de models diferents) per a que un usuari l'avaluï.

**Paràmetres de la URL:**
- `category_code` (string, obligatori): La categoria de la tasca sol·licitada (p. ex., `correccio`).
- `session_id` (string, obligatori): L'identificador de sessió de l'usuari per evitar repetir tasques.

**Resposta (200 OK):**
```json
{
  "prompt": "El gat es blau",
  "response_a": "El gat és blau.",
  "response_b": "El gat es color blau.",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```


### `POST /api/vote`

Registra el vot d'un usuari sobre una tasca prèviament demanada.

**Body (JSON):**
- `winner` (string): Quin model ha guanyat. Valors possibles: `"a"`, `"b"`, `"tie"` o `"neither"`.
- `token` (string): El JWT generat per l'endpoint `/api/task` (conté els IDs del prompt i les respostes).
 
**Resposta (200 OK):**
```json
{
  "status": "ok"
}
```


### `GET /api/ranking`

Retorna el rànquing actual de models. Pot filtrar per una categoria específica o
agregar totes les categories.

**Paràmetres de la URL:**
- `category_code` (string, opcional): El codi de la categoria a consultar. Si s'omet,
  retorna el rànquing global agregant totes les categories.

**Resposta (200 OK):**
```json
{
  "category_code": "correccio",
  "n_votes_total": 390,
  "n_votes_decisive": 358,
  "n_ties": 23,
  "n_neither": 9,
  "best_model": "gemma-3-4b-it",
  "ranked_models": [
    {
      "rank": 1,
      "model": "gemma-3-4b-it",
      "bt_skill": 0.27
    },
    {
      "rank": 2,
      "model": "qwen-3.5-9b",
      "bt_skill": -0.04
    },
    {
      "rank": 3,
      "model": "salamandra-7b-instruct",
      "bt_skill": -0.23
    }
  ],
  "confidence": {
    "category_code": "correccio",
    "best_model": "gemma-3-4b-it",
    "n_prompts": 10,
    "n_decisive_votes": 358,
    "p_best_is_best": 0.97,
    "confidence_interval": {
      "lo": 0.12,
      "hi": 0.44
    },
    "is_stable": true
  }
}
```

## Tests

```bash
uv run pytest -v
```

The tests need the PostgreSQL container running and run against `arena_cat_test`.

## Migrations

To evolve the schema:

1. Edit the models in `app/models.py`.
2. With the database at `head`, generate the migration:
   ```bash
   uv run alembic revision --autogenerate -m "description of the change"
   ```
3. **Review** the generated file in `migrations/versions/`. Autogeneration does not detect
   everything: renames show up as drop + create, and enum or `CHECK` changes are missed.
   It also does not drop `ENUM` types when dropping tables, so add that to `downgrade` by
   hand if you create new ones.
4. Apply the migration and check it can be reverted:
   ```bash
   uv run alembic upgrade head
   uv run alembic downgrade -1   # then go back to 'upgrade head'
   ```
5. Run the tests.

Useful commands:

```bash
uv run alembic current         # currently applied revision
uv run alembic history         # migration history
uv run alembic downgrade base  # undo all migrations
```

## Tooling

```bash
uv run ruff check .       # linting
uv run ruff format .      # formatting
```
