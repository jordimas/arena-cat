# Pla detallat — Fita 1: Prova de concepte

> Aquest document detalla la **Part 1** de la [Fita 1](../README.md#fita-1-prova-de-concepte): la construcció de la plataforma. Per a la motivació i la metodologia, vegeu [projecte.md](projecte.md). Per al resum executiu, vegeu el [full de ruta](../README.md#full-de-ruta).

## Continguts

- [Estratègia de lliurament en tres versions](#estratègia-de-lliurament-en-tres-versions)

---

## Estratègia de lliurament en tres versions

Per arribar abans a un sistema utilitzable, dividim la construcció en tres entregues incrementals:

### v1 — *Nucli tècnic provable en local*

L'objectiu és tenir el bucle de votació funcionant tan aviat com sigui possible, sense compromisos d'integració externa.

- **30 *prompts* curats i revisats**:
    - Definició dels criteris que ha de complir cada *prompt*: avaluable (resposta clarament comparable), no ambigu, curt (llegible en 1-2 minuts amb les dues respostes), específic del català (que no es resolgui amb coneixement genèric en anglès), prou difícil perquè els models hi puguin discrepar, i amb varietat de dificultat i registre dins de cada categoria.
    - Redacció en YAML, 10 per categoria (correcció, reformulació, traducció).
    - Revisió creuada (lingüista + segon revisor).
    - Versionat a `data/prompts/v1/` al repositori.
- **Inferència executada i desada**:
    - *Script* `scripts/inferencia.py` que itera sobre les 90 combinacions (3 models × 30 *prompts*).
    - Models de la prova de concepte: Qwen 3.8 27B, Mistral Small 3.2 24B Instruct i Gemma 3 27B Instruct.
    - Sempre que sigui possible, **un únic motor d'inferència** per als 3 models — preferentment Hugging Face Transformers, que tot i no ser el més ràpid és la implementació de referència i la que minimitza el risc d'introduir variacions no atribuïbles al model.
    - Bolcat dels 90 JSONs a `data/inferencies/v1/`.
    - Metadades: `seed`, `quantization` i `model_version` per garantir reproduïbilitat.
- **Servidor SQL provisionat**:
    - PostgreSQL 16+ aixecat via `docker compose` amb volum persistent.
    - Creació de la base de dades `arena_cat` i del rol d'aplicació amb permisos limitats.
    - Variables d'entorn al `.env` (`DATABASE_URL`, credencials), amb `.env.example` versionat.
- **Model de dades**:
    - Taules `users`, `prompts`, `responses` i `votes` definides amb SQLAlchemy.
    - Migracions amb Alembic per poder evolucionar l'esquema a v2.
    - Restriccions: `UNIQUE(prompt_id, model)` a `responses`, *enum* per `winner` (`a` | `b` | `tie` | `neither`).
    - Índexs sobre `votes.prompt_id`, `votes.created_at` i `votes.user_id`.
    - A `users`: `email` opcional i únic (per permetre l'esborrat en la baixa), `email_hash` únic per detectar re-registres sense guardar-lo en clar, `password_hash` (Argon2id), `deleted_at` per marcar baixes, i camps de consentiment (`consent_version`, `consent_at`).
- ***Script* de càrrega idempotent**:
    - `scripts/carrega_inferencies.py` que llegeix `data/prompts/v1/*.txt` i `data/inferencies/v1/**/*.json`.
    - Pobla les taules `prompts` i `responses` amb *upsert* per clau primària.
    - Es pot tornar a executar sense duplicar files.
- **Servei web FastAPI**:
    - Esquelet `backend/app/`: `main.py`, `models.py` (SQLAlchemy), `schemas.py` (Pydantic), `db.py`, `routers/`.
    - Dependències a `pyproject.toml` (gestió amb uv o poetry): `fastapi`, `uvicorn`, `sqlalchemy`, `psycopg`, `alembic`, `pydantic`, `pyyaml`.
    - CORS permissiu en mode desenvolupament perquè l'HTML local pugui cridar l'API.
    - Arrencada amb `uvicorn app.main:app --reload --port 8000`.
- **Registre i baixa d'usuaris (compatible amb el RGPD)**:
    - **Alta**: `POST /api/auth/register` amb `email`, `contrasenya` i `consent_version`. Validació de format d'email, força mínima de la contrasenya i verificació explícita del consentiment (casella no premarcada) sobre el tractament de dades i la [política de privadesa](#politica-de-privadesa). Es desa `password_hash` (Argon2id) i `email_hash` (HMAC-SHA256 amb *pepper* de servidor) per poder bloquejar re-registres amb el mateix correu quan un usuari s'ha donat de baixa.
    - **Verificació d'email**: enllaç signat (JWT curt) validat a `POST /api/auth/verify`; només després l'usuari pot votar. Serveix per **(a)** impedir el registre amb correus de tercers sense consentiment, **(b)** ancorar la identitat i frenar les altes massives com a defensa contra el vandalisme del rànquing, i **(c)** garantir un canal de contacte fiable per a la recuperació de compte i l'exercici de drets RGPD. **A v1 la implementem com a stub** — l'enllaç s'imprimeix al *log* del servidor en desenvolupament, sense SMTP real — per no arrossegar la infraestructura de correu (servidor, DKIM/SPF, plantilles, rebots, reputació de domini) fins que sigui necessari. L'enviament real de correu s'activa a v2, en obrir la plataforma a voluntaris.
    - **Inici i tancament de sessió**: `POST /api/auth/login` retorna una *cookie* de sessió `HttpOnly; Secure; SameSite=Lax` amb ID de sessió opac (no JWT en client) i caducitat curta amb renovació. `POST /api/auth/logout` la revoca.
    - **Baixa (dret de supressió)**: `POST /api/auth/delete-account` autenticat amb la contrasenya actual. Executa una **anonimització** dins d'una transacció:
        - Es **preserva `users.id`** i les files de `votes` associades — l'agregació estadística del rànquing no ha de perdre informació.
        - S'esborren els camps identificatius: `email`, `email_hash`, `password_hash`, `consent_at`, `last_login_at`; `deleted_at` passa a `NOW()` i es revoquen totes les sessions.
        - Els *logs* de servidor amb IP i *user-agent* del compte tenen una retenció màxima de 30 dies (rotació automàtica); no es guarden en cap taula lligada a `user_id`.
    - **Exportació de dades (dret d'accés/portabilitat)**: `GET /api/auth/export` retorna un JSON amb les dades personals i els vots emesos pel compte.
    - **Política de privadesa**: pàgina estàtica que explica base legal (consentiment, art. 6.1.a RGPD), finalitats (avaluació de models, agregació estadística anonimitzada, publicació de resultats agregats), retenció, responsable del tractament (Softcatalà) i drets ARSOPL. El text de la política es versiona i `consent_version` referencia la versió acceptada.
    - **Aturades tècniques**:
        - *Rate limit* estricte a `register`, `login` i `verify` (per IP i per email).
        - No es fa *logging* de contrasenyes ni de tokens; els emails només apareixen en *logs* d'errors quan és estrictament necessari (i mai a nivell INFO).
        - Camps sensibles xifrats en repòs a nivell de disc (backup i base de dades); còpies de seguretat amb la mateixa retenció que la BD i esborrat coordinat amb la baixa.
- **`GET /api/task`**:
    - Tria un *prompt* aleatori i dues respostes de models diferents per aquell *prompt*. Si es passa `category_code`, limita la tria a aquella categoria; si no, busca la propera tasca disponible entre totes les categories.
    - Aleatoritza l'ordre A/B per evitar biaix de posició.
    - Signa un `token_vot` HMAC que codifica els identificadors i un *timestamp*.
    - Retorna `category_code`, el *prompt*, les dues respostes i el *token*.
- **`POST /api/task/skip`**:
    - Requereix sessió autenticada i el `token_vot` de la tasca.
    - Desa una fila a `task_skips` perquè aquella parella no es torni a oferir al mateix usuari.
- **`GET /api/task/progress`**:
    - Requereix sessió autenticada.
    - Retorna el progrés global de l'usuari (`total`, `voted`, `skipped`, `remaining`) sobre totes les categories.
- **`POST /api/vote`**:
    - Requereix sessió autenticada (usuari donat d'alta i amb email verificat).
    - Verifica el `token_vot` (signatura i caducitat).
    - Valida el cos amb Pydantic (`winner ∈ {a, b, tie, neither}`).
    - Insereix una fila a `votes` amb `user_id` i `response_time_s`. Els vots d'usuaris posteriorment donats de baixa **es conserven** referenciant l'`user_id` (ja anonimitzat), preservant la validesa del rànquing.
    - *Rate limit* per `user_id` i per IP.
- **HTML local de proves** (`frontend/dev/index.html`):
    - Una sola pàgina amb HTML + JS petit, sense *framework*.
    - Formularis d'alta, verificació d'email, inici de sessió i baixa de compte contra `/api/auth/*`.
    - Un cop autenticat, crida `GET /api/task` i renderitza el *prompt* amb les dues respostes.
    - Quatre botons (A millor / B millor / empat / cap) que envien `POST /api/vote`.
    - Servida amb `python -m http.server` o oberta directament. No per a usuaris finals.
- **Proves**:
    - Tests unitaris dels *endpoints* d'autenticació i votació amb `pytest` + `httpx.AsyncClient`.
    - Test específic del flux de baixa: dona d'alta un usuari, emet vots, executa la baixa, i verifica que (a) `email`, `email_hash` i `password_hash` són `NULL`, (b) `deleted_at` està establert, (c) el mateix email no permet re-registrar-se, i (d) les files de `votes` continuen presents amb l'`user_id` original.
    - Prova manual end-to-end: aixecar Postgres + FastAPI + l'HTML local, registrar-se, verificar el correu, fer ~20 vots i tancar amb una baixa.
    - `README.md` del *backend* amb instruccions d'arrencada en local i notes sobre el compliment del RGPD.

- **Integració amb la web de Softcatalà**:
    - Un cop el nucli tècnic funcioni end-to-end amb usuaris, es crea la **interfície definitiva integrable a la web de Softcatalà**, amb l'estil i el *layout* del lloc principal, llesta per obrir-se a la comunitat.
    - La integració es fa al repositori del WordPress de Softcatalà — [Softcatala/wp-softcatala](https://github.com/Softcatala/wp-softcatala) — escrivint **una plantilla PHP** que crida al **microservei FastAPI** d'Arena Cat. La plantilla viu dins del tema de WordPress; el *backend* d'Arena Cat es desplega com a microservei independent i la plantilla en consumeix els *endpoints* (`/api/task`, `/api/vote`, `/api/ranking`, autenticació).
    - Softcatalà disposa de **maquinari propi** on es poden desplegar **contenidors Docker**, de manera que el microservei FastAPI d'Arena Cat es pot empaquetar i executar directament sobre aquesta infraestructura sense dependre de proveïdors externs. A més, Softcatalà manté un **GitLab intern que fa de mirall dels repositoris públics de GitHub**, cosa que permet integrar el desplegament del microservei amb el flux de CI/CD habitual de l'organització.
    - Plantilla PHP nova al tema de [wp-softcatala](https://github.com/Softcatala/wp-softcatala) que renderitza la pàgina d'avaluació amb el *layout* habitual de softcatala.org (capçalera, peu, tipografia, colors).
    - Crides al microservei: des del PHP (servidor a servidor) per al *render* inicial i des del navegador (JS lleuger) per a les accions interactives.
    - Gestió d'autenticació coordinada entre WordPress i el microservei: opció A — Arena Cat manté el seu propi registre i el WP només l'embolcalla; opció B — *single sign-on* via el WordPress de Softcatalà. **Decisió pendent**.
    - CORS, CSP i *cookie policy* d'acord amb les normes del lloc principal.
    - Pàgines auxiliars dins de WordPress: presentació del projecte, instruccions per als avaluadors, FAQ.

> **Per què v1 amb usuaris i integració web des del principi**: el registre i la baixa afecten directament l'esquema de la BD i la mecànica dels vots (autenticació requerida, preservació de l'`user_id` en la baixa). Encaixar-ho ja des de v1 evita una migració complicada més endavant i valida el compliment del RGPD abans d'obrir la plataforma a voluntaris. La integració amb WordPress s'inclou també a v1 perquè és el camí crític per poder obrir la plataforma; l'HTML de proves cobreix el desenvolupament local mentre s'estabilitza.

### v2 — *Plataforma completa*

- Test de qualificació de 5 preguntes lligat al perfil d'usuari creat a v1.
- Contracte de rànquing (`GET /api/ranking`) amb estadístiques agregades i pàgina pública de progrés.
- Detecció d'abusos i ponderació de contribucions sobre el lligam vot↔usuari ja existent.
- Indicador d'objectiu i progrés a la pàgina d'avaluació.
- **Enviament real de correu per a la verificació d'email** (SMTP, DKIM/SPF, plantilles, gestió de rebots), substituint l'stub de v1.
- Recuperació de contrasenya i canvi d'email amb re-verificació.
