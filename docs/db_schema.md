# Database schema

ER diagram of the Arena Cat data schema.

> Source: [`backend/app/models.py`](../backend/app/models.py).

```mermaid
erDiagram
    categories {
        integer id PK
        varchar(64) code
        varchar(128) name
        text description
        text evaluation_instructions
    }

    prompts {
        integer id PK
        varchar(32) version
        varchar(64) code
        integer category_id FK
        text text
        timestamptz created_at
    }

    responses {
        integer id PK
        integer prompt_id FK
        varchar(128) model
        text text
        jsonb inference_metadata
        timestamptz created_at
    }

    users {
        bigint id PK
        varchar(255) email
        varchar(64) email_hash
        text password_hash
        timestamptz email_verified_at
        varchar(32) consent_version
        timestamptz consent_at
        timestamptz created_at
        timestamptz deleted_at
    }

    sessions {
        bigint id PK
        bigint user_id FK
        varchar(64) token_hash
        timestamptz created_at
        timestamptz expires_at
        timestamptz revoked_at
    }

    votes {
        bigint id PK
        integer prompt_id FK
        bigint user_id FK
        integer response_a_id FK
        integer response_b_id FK
        winner winner
        varchar(128) session_id
        numeric(6_2) response_time_s
        timestamptz created_at
    }

    task_skips {
        bigint id PK
        integer prompt_id FK
        bigint user_id FK
        integer response_a_id FK
        integer response_b_id FK
        timestamptz created_at
    }

    categories ||--o{ prompts : "category_id"
    prompts ||--o{ responses : "prompt_id"
    prompts ||--o{ votes : "prompt_id"
    prompts ||--o{ task_skips : "prompt_id"
    users ||--o{ sessions : "user_id"
    users ||--o{ votes : "user_id"
    users ||--o{ task_skips : "user_id"
    responses ||--o{ votes : "response_a_id"
    responses ||--o{ votes : "response_b_id"
    responses ||--o{ task_skips : "response_a_id"
    responses ||--o{ task_skips : "response_b_id"
```

## Constraints and indexes

| Table | Type | Name | Definition |
|-------|------|------|------------|
| categories | UNIQUE | — | `(code)` |
| prompts | UNIQUE | `uq_prompts_version_code` | `(version, code)` |
| prompts | FK | — | `category_id → categories.id` |
| responses | UNIQUE | `uq_responses_prompt_model` | `(prompt_id, model)` |
| responses | UNIQUE | `uq_responses_prompt_id_id` | `(prompt_id, id)` |
| responses | FK | — | `prompt_id → prompts.id` `ON DELETE CASCADE` |
| users | UNIQUE | — | `(email)` |
| users | UNIQUE | — | `(email_hash)` |
| users | CHECK | `ck_users_active_have_credentials` | `deleted_at IS NOT NULL OR (email IS NOT NULL AND email_hash IS NOT NULL AND password_hash IS NOT NULL AND consent_at IS NOT NULL)` |
| sessions | FK | — | `user_id → users.id` |
| sessions | UNIQUE | — | `(token_hash)` |
| sessions | INDEX | `ix_sessions_user_id` | `user_id` |
| votes | CHECK | `ck_votes_responses_different` | `response_a_id <> response_b_id` |
| votes | FK | — | `prompt_id → prompts.id` |
| votes | FK | `fk_votes_user_id_users` | `user_id → users.id` `ON DELETE SET NULL` |
| votes | FK | `fk_votes_response_a` | `(prompt_id, response_a_id) → responses(prompt_id, id)` |
| votes | FK | `fk_votes_response_b` | `(prompt_id, response_b_id) → responses(prompt_id, id)` |
| votes | INDEX | `ix_votes_prompt_id` | `prompt_id` |
| votes | INDEX | `ix_votes_created_at` | `created_at` |
| votes | INDEX | `ix_votes_user_id` | `user_id` |
| votes | UNIQUE INDEX | `uq_votes_user_prompt_pair` | `(user_id, prompt_id, least(response_a_id, response_b_id), greatest(response_a_id, response_b_id))` |
| task_skips | CHECK | `ck_task_skips_responses_different` | `response_a_id <> response_b_id` |
| task_skips | FK | — | `prompt_id → prompts.id` |
| task_skips | FK | — | `user_id → users.id` |
| task_skips | FK | `fk_task_skips_response_a` | `(prompt_id, response_a_id) → responses(prompt_id, id)` |
| task_skips | FK | `fk_task_skips_response_b` | `(prompt_id, response_b_id) → responses(prompt_id, id)` |
| task_skips | INDEX | `ix_task_skips_user_id` | `user_id` |
| task_skips | UNIQUE INDEX | `uq_task_skips_user_prompt_pair` | `(user_id, prompt_id, least(response_a_id, response_b_id), greatest(response_a_id, response_b_id))` |

## Enums

- **`winner`**: `a`, `b`, `tie`, `neither`
