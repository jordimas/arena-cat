
## Executar la canonada d'inferència

El projecte fa servir [`uv`](https://docs.astral.sh/uv/) per gestionar l'entorn Python i les dependències.

### 1. Instal·lar `uv`

Si no el tens instal·lat:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Crear l'entorn i instal·lar dependències

Des de l'arrel del repositori:

```bash
uv sync --group inference
```

Això crea l'entorn virtual `.venv/` i instal·la les dependències d'inferència definides a `pyproject.toml`.

### 3. Configurar el token de Hugging Face

L'script llegeix el token des de la variable d'entorn `HF_TOKEN`. Exporta-la
amb el mecanisme que prefereixis per al teu entorn:

```bash
export HF_TOKEN=hf_el_teu_token
```

També pots carregar-la amb eines externes com `dotenv`, `direnv`, el teu shell,
CI o Docker. Si fas servir un fitxer `.env`, mantén-lo local i no el versionis.

El fitxer `.env.example` documenta les variables d'entorn esperades:

```bash
cp .env.example .env
```

### 4. Executar els tests

```bash
uv run --group inference python -m unittest discover -s tests
```

Els tests fan servir dobles de prova (*mocks/stubs*) per al model i el tokenitzador, de manera que no descarreguen models de Hugging Face.

### 5. Executar la inferència

Abans d'executar-la, revisa `config/inferencia/inferencia_config.yaml` i comprova que els models, els paràmetres de generació i els prompts són els esperats.

```bash
uv run --group inference python scripts/inferencia.py
```

Per sobreescriure el dispositiu (device-map) definit a la configuració:

```bash
uv run --group inference python scripts/inferencia.py --device-map cpu
```
Si més endavant cal controlar altres opcions adicionals, com el dtype o la quantització,
es poden afegir nous paràmetres específics.


Per fer servir un fitxer de configuració alternatiu:

```bash
uv run --group inference python scripts/inferencia.py --config config/inferencia/inferencia_local_config.yaml
```

Els missatges de progrés fan servir `logging` i es poden reduir ajustant el
nivell de logging.


Per veure només avisos i errors:

```bash
uv run --group inference python scripts/inferencia.py --log-level WARNING
```

Els resultats es desen a `data/inferencies/v1/<model_id>/`.

### 6. Mètriques de distància entre sortides

`scripts/metriques.py` calcula com de diferents són les sortides dels diferents
models per a un mateix prompt. Serveix per detectar prompts on els models
generen respostes massa semblants — i, per tant, on un avaluador humà no podrà
distingir-les fàcilment.

Per a cada parella de models imprimeix dues mètriques **normalitzades a
distància** (0 = sortides idèntiques, 1 = totalment diferents) i la seva
mitjana:

- **chrF_d**: `1 − chrF/100` (n-grames de caràcters).
- **edit**: Levenshtein normalitzat a caràcter.
- **combinat**: mitjana de les dues anteriors, com a resum d'un cop d'ull.

A més de la mitjana de les parelles, mostra la **parella pitjor** (la més
semblant del trio, mínim de les distàncies), que delata si dos models continuen
sonant igual encara que la mitjana sigui alta.

```bash
uv run python scripts/metriques.py
uv run python scripts/metriques.py --inferencies data/inferencies/hypotheses
```

### 7. Prova local amb un model molt petit

Per comprovar el flux complet sense carregar cap model gran, pots usar la configuració local:

```bash
uv run --group inference python scripts/inferencia.py --config config/inferencia/inferencia_local_config.yaml
```

Aquesta configuració fa servir `hf-internal-testing/tiny-random-gpt2`, un model de prova molt petit. Serveix per validar que la descàrrega, la càrrega del model, la generació i l'escriptura dels YAML funcionen, però no per avaluar qualitat lingüística.

### 8. Carregar prompts i inferències a la base de dades

`scripts/carrega_inferencies.py` sincronitza `data/prompts/categories.yaml`, la font de veritat de les categories, amb la taula `categories` i publica els fitxers disponibles localment a les taules `prompts` i `responses`. Llegeix els prompts de `data/prompts/v1/*.txt` (text pla, clau `(version, code)`, on `code` és el nom del fitxer i la categoria es dedueix del prefix, p. ex. `traduccio_1` -> `traduccio`) i les inferències de `data/inferencies/v1/<model_id>/*.yaml` (clau `(prompt_id, model)`). El raonament intern es desa a les metadades, no al text visible, perquè l'avaluació és a cegues.

Les inferències de referència es conserven a la branca `dades_inferencia`, separades de les branques de codi. El target `make load_reference_inferences` crea o reutilitza un worktree paral·lel i apunta el carregador a les dades de referència.

És **idempotent**: tornar-lo a executar no duplica files. Cada fila es classifica com a inserida o omesa (ja existeix amb el mateix contingut), i n'imprimeix un resum a la sortida estàndard. **No modifica files existents**: si un prompt o una resposta ja existeix amb la mateixa clau però amb un contingut diferent, ho registra com a error i exigeix publicar-ho amb una versió nova en comptes de sobreescriure-ho. Sobreescriure el text d'una resposta invalidaria semànticament els vots que hi apunten (mantenen el `response_id` però votaven un text que hauria canviat). Igualment, si un fitxer no compleix l'esquema (categoria o prompt desconegut, camps obligatoris absents, YAML d'inferència mal format, o prompt buit), registra un error clar. En tots els casos d'error acaba amb codi de sortida 1 sense aturar la resta de la càrrega.

Necessita la base de dades en marxa i migrada, i les mateixes variables de connexió que el backend (vegeu `.env`). La manera més curta és el target del `Makefile`, des de l'arrel del repositori:

```bash
make load_inferences
```

Per defecte usa `data/prompts/categories.yaml`, `data/prompts/v1` i `data/inferencies/v1`. Es poden sobreescriure els directoris i la versió amb variables d'entorn:

```bash
PROMPTS_DIR=data/prompts/v2 INFERENCIES_DIR=data/inferencies/v2 make load_inferences
```

Per usar el worktree de `dades_inferencia`:

```bash
make load_reference_inferences
```

El target `load_inferences` només és un embolcall d'aquesta comanda, que reaprofita l'entorn i el model de dades del backend amb `--project backend` i també es pot cridar directament:

```bash
uv --project backend run python scripts/carrega_inferencies.py \
    --prompts-dir data/prompts/v1 \
    --inferencies-dir data/inferencies/v1
```
