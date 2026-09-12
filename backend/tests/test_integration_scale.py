"""Tests d'integració a escala per a la biblioteca de rànquing.

Aquests tests fan córrer el pipeline complet — sampler → vots → DB →
compute_ranking → assess_confidence — sobre escenaris realistes amb
dades plantades, per detectar que tot encaixa quan creix el volum.

Tots els tests són deterministes (llavors fixes) perquè es puguin
executar a la CI sense flakiness. Cada escenari planta una "veritat"
i comprova que el sistema la descobreix.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select

from app.models import Category, Prompt, Response, User, Vote, Winner
from app.ranking.confidence import assess_confidence
from app.ranking.ranking import compute_ranking
from app.ranking.sampler import select_next_task
from app.security import compute_email_hash, hash_password

MODELS = ["gemma-3-4b-it", "qwen-3.5-9b", "salamandra-7b-instruct"]


# ---------------------------------------------------------------------------
# Helpers compartits per tots els tests d'aquest fitxer.
# ---------------------------------------------------------------------------


def _category(session, code):
    return session.scalar(select(Category).where(Category.code == code))


def _seed_prompts_with_responses(session, category_code, n_prompts):
    """Crea n_prompts en una categoria, cadascun amb una resposta per cada model."""
    cat = _category(session, category_code)
    out = []
    for i in range(n_prompts):
        prompt = Prompt(
            version="vtest",
            code=f"{category_code}-scale-{i:02d}",
            category_id=cat.id,
            text=f"Prompt {i} a {category_code}",
        )
        session.add(prompt)
        session.flush()
        responses = {}
        for m in MODELS:
            r = Response(prompt=prompt, model=m, text=f"Resposta de {m} a {prompt.code}")
            session.add(r)
            responses[m] = r
        session.flush()
        out.append((prompt, responses))
    return out


def _planted_winner(rng, model_a, model_b, favored_model, win_prob):
    """Retorna 'a' o 'b' segons una probabilitat plantada.

    Si `favored_model` és en la parella, guanya amb `win_prob`.
    Si no hi és (parella entre no favorits), el resultat és 50/50.
    """
    if favored_model == model_a:
        return Winner.a if rng.random() < win_prob else Winner.b
    if favored_model == model_b:
        return Winner.b if rng.random() < win_prob else Winner.a
    # Parella sense el favorit: 50/50.
    return Winner.a if rng.random() < 0.5 else Winner.b


def _record_vote(
    db_session, prompt_id, response_a_id, response_b_id, winner, session_id=None, user_id=None
):
    """Insereix un vot a la base de dades de tests."""
    db_session.add(
        Vote(
            prompt_id=prompt_id,
            response_a_id=response_a_id,
            response_b_id=response_b_id,
            winner=winner,
            session_id=session_id,
            user_id=user_id,
        )
    )
    db_session.flush()


def _create_user(db_session, email):
    """Crea un usuari actiu i verificat i retorna la instància."""
    user = User(
        email=email,
        email_hash=compute_email_hash(email),
        password_hash=hash_password("ContrasenyaSegura123!"),
        consent_version="v1",
        consent_at=datetime.now(UTC),
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    db_session.flush()
    return user


def _assert_running_against_test_database(db_session):
    """Cinturó de seguretat: refusa córrer contra una base de dades que no sigui de tests.

    La fixture `session` del conftest ja apunta a `arena_cat_test`, però
    comprovem-ho explícitament a cada test d'integració per defensa en
    profunditat. Si algú modifica el conftest per error i fa que els tests
    apuntin a producció, aquesta assertion ho atura abans d'inserir res.
    """
    db_name = db_session.bind.engine.url.database
    assert db_name.endswith("_test"), (
        f"REFUSANT executar el test contra la base '{db_name}'. "
        f"Aquest test només pot córrer contra una base de dades acabada en '_test'."
    )


def _planted_winner_with_noise(
    rng, model_a, model_b, favored_model, win_prob, tie_rate, neither_rate
):
    """Com _planted_winner, però introdueix empats i 'neither' amb probabilitats fixes.

    Args:
        rng: numpy default_rng.
        model_a, model_b: identificadors dels dos models de la parella.
        favored_model: el model que volem afavorir entre vots decisius.
        win_prob: probabilitat que favored_model guanyi quan és a la parella.
        tie_rate: probabilitat que un vot sigui empat.
        neither_rate: probabilitat que un vot sigui 'neither'.

    El resultat és estocàstic; la reproduïbilitat ve donada per la llavor del rng.
    """
    roll = rng.random()
    if roll < tie_rate:
        return Winner.tie
    if roll < tie_rate + neither_rate:
        return Winner.neither
    # Vot decisiu: reutilitzem la lògica de _planted_winner.
    return _planted_winner(rng, model_a, model_b, favored_model, win_prob)


# ---------------------------------------------------------------------------
# Test 1 — un model preferit al 60% és identificat correctament.
# ---------------------------------------------------------------------------


def test_60pct_winner_is_correctly_identified(session):
    """Plantem gemma com a favorita al 60%. El pipeline ha d'identificar-la com a millor.

    Configuració:
        - Categoria: correcció.
        - 5 prompts × 3 parelles = 15 cel·les.
        - 1800 vots simulats (~120 vots/cel·la), distribuïts pel sampler quota-balanced.
        - gemma guanya amb probabilitat 0.6 quan és a la parella.
        - qwen vs salamandra: 50/50 (cap dels dos és el favorit).

    Comprovacions:
        - `compute_ranking` reporta gemma com a `best_model`.
        - El skill BT de gemma és estrictament superior als dels altres.
        - `assess_confidence` reporta `is_stable=True` (el rànquing és sòlid).
        - `p_best_is_best` és alt (≥ 0.95).
    """
    _assert_running_against_test_database(session)
    _seed_prompts_with_responses(session, "correccio", n_prompts=5)
    favored_model = "gemma-3-4b-it"
    win_prob = 0.60
    # 5 prompts × 3 parelles = 15 cel·les. Amb 1800 vots són ~120 per cel·la:
    # més que suficient perquè la CI de rank1-vs-rank2 quedi clarament positiva
    # amb un avantatge plantat del 60% i només 5 clusters de prompt.
    n_votes = 1800

    # Ni el RNG ens atura. Som i serem
    rng = np.random.default_rng(seed=1714)

    for i in range(n_votes):
        # Cada crida al sampler es passa amb una llavor diferent perquè
        # la randomització dels empats al recompte mínim cobreixi totes les cel·les.
        task = select_next_task(session, "correccio", seed=i)
        assert task is not None, f"Sampler ha retornat None a la iteració {i}"

        # Identifiquem quins models hi ha a la parella mostrada.
        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])

        # Decidim el guanyador segons la veritat plantada.
        winner = _planted_winner(rng, response_a.model, response_b.model, favored_model, win_prob)

        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            session_id=f"scale-test-{i % 30}",  # 30 sessions diferents
        )

    # Pas final: comprovem que el pipeline descobreix la veritat plantada.
    ranking = compute_ranking(session, "correccio")
    confidence = assess_confidence(session, "correccio", n_bootstrap=200, seed=1714)

    # gemma ha de ser la millor.
    assert ranking["best_model"] == favored_model, (
        f"Esperat best_model={favored_model}, obtingut {ranking['best_model']}. "
        f"Skills: {ranking['bt_skills']}"
    )
    assert confidence["best_model"] == favored_model

    # El skill de gemma ha de ser estrictament superior als altres.
    skills = ranking["bt_skills"]
    for other in MODELS:
        if other == favored_model:
            continue
        assert skills[favored_model] > skills[other], (
            f"Skill de {favored_model} ({skills[favored_model]}) no supera "
            f"el de {other} ({skills[other]})"
        )

    # El rànquing ha de ser estable amb 600 vots i un avantatge plantat del 60%.
    assert confidence["is_stable"] is True, (
        f"Rànquing inestable amb un guanyador clar plantat. "
        f"CI=[{confidence['ci_lo']}, {confidence['ci_hi']}], "
        f"p_best_is_best={confidence['p_best_is_best']}"
    )

    # La confiança que el millor actual sigui realment el millor ha de ser alta.
    assert confidence["p_best_is_best"] >= 0.95, (
        f"p_best_is_best={confidence['p_best_is_best']} massa baix per a un escenari "
        f"amb un guanyador plantat al 60%"
    )

    # No hi ha d'haver cap cicle: gemma guanya tothom, no es pot tancar A>B>C>A.
    assert ranking["cycle_detected"] is False


# ---------------------------------------------------------------------------
# Test 2 — molts empats i 'neither' no distorsionen BT.
# ---------------------------------------------------------------------------


def test_high_tie_and_neither_rates_dont_distort_bt(session):
    """Plantem un guanyador clar enmig de molt soroll d'empats i 'neithers'.

    Aquesta prova garanteix que la nostra política — descartar empats i
    'neithers' del càlcul BT i només reportar-los a part — és sòlida.
    Si algú "millorés" el codi comptant els empats com a 0.5/0.5, aquesta
    prova fallaria: el guanyador plantat perdria el seu avantatge.

    Configuració:
        - Categoria: traducció.
        - 5 prompts × 3 parelles = 15 cel·les.
        - 800 vots simulats. Després del soroll:
            · ~30% empats (Winner.tie).
            · ~15% 'neithers' (Winner.neither).
            · ~55% decisius — d'aquests, gemma guanya al 60% quan és a la parella.
        - Total decisius esperat: ~440 (≈ 29 per cel·la).

    Comprovacions:
        - `best_model == gemma` malgrat el soroll.
        - Els recomptes `n_ties` i `n_neither` cauen a la franja esperada.
        - El rànquing és estable (`is_stable=True`).
        - El skill BT de gemma és estrictament superior al dels altres.
    """
    _assert_running_against_test_database(session)
    _seed_prompts_with_responses(session, "traduccio", n_prompts=5)
    favored_model = "gemma-3-4b-it"
    win_prob = 0.60
    tie_rate = 0.30
    neither_rate = 0.15
    n_votes = 800

    # Ni el RNG ens atura. Som i serem
    rng = np.random.default_rng(seed=1714)

    for i in range(n_votes):
        task = select_next_task(session, "traduccio", seed=i)
        assert task is not None, f"Sampler ha retornat None a la iteració {i}"

        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])

        winner = _planted_winner_with_noise(
            rng=rng,
            model_a=response_a.model,
            model_b=response_b.model,
            favored_model=favored_model,
            win_prob=win_prob,
            tie_rate=tie_rate,
            neither_rate=neither_rate,
        )

        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            session_id=f"scale-test-noise-{i % 30}",
        )

    ranking = compute_ranking(session, "traduccio")
    confidence = assess_confidence(session, "traduccio", n_bootstrap=200, seed=1714)

    # 1) Els empats i 'neithers' han arribat a la franja esperada.
    #    Donem un marge de ±5 punts percentuals respecte al pla.
    tie_fraction = ranking["n_ties"] / ranking["n_votes_total"]
    neither_fraction = ranking["n_neither"] / ranking["n_votes_total"]
    assert abs(tie_fraction - tie_rate) < 0.05, (
        f"Fracció d'empats {tie_fraction:.2%} massa lluny del plantat {tie_rate:.0%}"
    )
    assert abs(neither_fraction - neither_rate) < 0.05, (
        f"Fracció de 'neither' {neither_fraction:.2%} massa lluny del plantat {neither_rate:.0%}"
    )

    # 2) El recompte de decisius coincideix amb la resta dels vots.
    assert (
        ranking["n_votes_decisive"]
        == ranking["n_votes_total"] - ranking["n_ties"] - ranking["n_neither"]
    )

    # 3) Malgrat el soroll, gemma continua sent la millor.
    assert ranking["best_model"] == favored_model, (
        f"BT s'ha distorsionat: esperat {favored_model}, obtingut {ranking['best_model']}. "
        f"Skills: {ranking['bt_skills']}"
    )

    # 4) El skill de gemma supera estrictament els altres.
    skills = ranking["bt_skills"]
    for other in MODELS:
        if other == favored_model:
            continue
        assert skills[favored_model] > skills[other], (
            f"Skill de {favored_model} ({skills[favored_model]}) no supera "
            f"el de {other} ({skills[other]}) sota soroll"
        )

    # 5) El rànquing és estable: BT confia en el guanyador encara que el 45% dels
    #    vots no aporta direcció.
    assert confidence["is_stable"] is True, (
        f"Rànquing inestable sota soroll d'empats/'neithers'. "
        f"CI=[{confidence['ci_lo']}, {confidence['ci_hi']}], "
        f"p_best_is_best={confidence['p_best_is_best']}"
    )

    # 6) p_best_is_best alt: la confiança no es desploma per la presència de soroll.
    assert confidence["p_best_is_best"] >= 0.90, (
        f"p_best_is_best={confidence['p_best_is_best']} massa baix sota soroll"
    )


# ---------------------------------------------------------------------------
# Test 3 — moltes sessions entrellaçades; cap sessió no veu dues vegades la
#          mateixa (prompt, parella).
# ---------------------------------------------------------------------------


def test_no_session_repeats_a_pair_in_multi_session_campaign(session):
    """30 sessions voten alhora. Cap sessió no pot veure la mateixa cel·la dues vegades.

    Aquest test cobreix el que els tests unitaris de `test_sampler.py` no
    poden capturar: el comportament del sampler quan moltes sessions
    independents comparteixen la mateixa base de dades i alternen els seus
    vots. Si el filtre per `session_id` patís un error subtil (p.ex.,
    oblidar la clàusula WHERE i barrejar vots de totes les sessions),
    aquest escenari el detectaria.

    Configuració:
        - Categoria: reformulació.
        - 5 prompts × 3 parelles = 15 cel·les.
        - 30 sessions actives, cadascuna fent ~20 vots (600 vots totals).
        - Les sessions s'alternen: vot 1 = sessio-0, vot 2 = sessio-1, ...
          vot 30 = sessio-29, vot 31 = sessio-0 amb la BD ja modificada
          per les 30 anteriors.

    Invariants:
        - Cada sessió no veu mai dues vegades la mateixa (prompt, parella).
        - Cada sessió toca, com a màxim, 15 cel·les diferents (límit
          natural: el nombre total de cel·les disponibles).
        - Quan una sessió arriba al límit, el sampler retorna None per a
          aquella sessió (i les altres no en queden afectades).
    """
    _assert_running_against_test_database(session)
    _seed_prompts_with_responses(session, "reformulacio", n_prompts=5)
    n_sessions = 30
    n_votes = 600
    favored_model = "gemma-3-4b-it"
    win_prob = 0.60

    # Ni el RNG ens atura. Som i serem
    rng = np.random.default_rng(seed=1714)

    # Un usuari real per sessió (la FK votes.user_id -> users.id ho exigeix).
    users = [_create_user(session, f"scale-multi-{s:02d}@example.com") for s in range(n_sessions)]

    # Per a cada usuari, mantenim un set de cel·les que ja ha vist.
    # Si en algun moment el sampler retorna una cel·la repetida, fallem.
    cells_seen_by_session: dict[int, set[tuple[int, str, str]]] = {user.id: set() for user in users}
    skipped_full_sessions = 0

    for i in range(n_votes):
        user = users[i % n_sessions]
        user_id = user.id

        task = select_next_task(session, "reformulacio", user_id=user_id, seed=i)

        # Si l'usuari ja ha completat totes les cel·les, el sampler retorna None.
        # Comptem aquests casos per assegurar-nos que el límit es respecta de manera neta.
        if task is None:
            skipped_full_sessions += 1
            assert len(cells_seen_by_session[user_id]) == 15, (
                f"Sampler ha retornat None per a l'usuari {user_id} però la sessió només "
                f"ha vist {len(cells_seen_by_session[user_id])} cel·les (de 15)"
            )
            continue

        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])
        cell = (task["prompt_id"], *sorted((response_a.model, response_b.model)))

        # INVARIANT CRÍTIC: aquest usuari no ha vist mai aquesta cel·la.
        assert cell not in cells_seen_by_session[user_id], (
            f"VIOLACIÓ: l'usuari {user_id} ha rebut una cel·la repetida {cell}. "
            f"Aquest vot és el número {i}; l'usuari ja havia vist "
            f"{len(cells_seen_by_session[user_id])} cel·les."
        )
        cells_seen_by_session[user_id].add(cell)

        winner = _planted_winner(rng, response_a.model, response_b.model, favored_model, win_prob)
        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            user_id=user_id,
        )

    # Comprovacions post-campanya:
    # 1) Cada usuari ha vist ≤ 15 cel·les úniques (no podria veure'n més).
    for uid, seen in cells_seen_by_session.items():
        assert len(seen) <= 15, (
            f"L'usuari {uid} ha vist {len(seen)} cel·les úniques (>15, impossible)"
        )

    # 2) Amb 30 usuaris repartint-se 600 vots, cada usuari rep ~20 vots.
    #    Però com que només hi ha 15 cel·les, alguns usuaris hauran arribat
    #    al sostre i hauran rebut None. Comprovem que això és coherent.
    total_unique_cells_seen = sum(len(s) for s in cells_seen_by_session.values())
    assert total_unique_cells_seen + skipped_full_sessions == n_votes, (
        f"No quadra: vots útils ({total_unique_cells_seen}) + sessions plenes "
        f"({skipped_full_sessions}) ≠ total ({n_votes})"
    )


# ---------------------------------------------------------------------------
# Test 4 — categories independents: guanyadors diferents no es contaminen.
# ---------------------------------------------------------------------------


def test_categories_are_independent_at_scale(session):
    """Plantem guanyadors diferents a categories diferents. No hi ha contaminació.

    Aquesta prova detecta la fuga entre categories. Si algú modifiqués les
    consultes SQL i oblidés filtrar per `category_code`, els vots d'una
    categoria influirien el rànquing d'una altra. Com que aquí plantem
    gemma com a guanyadora de correcció i qwen com a guanyadora de
    traducció, qualsevol fuga faria fallar les asserts.

    També comprovem que una tercera categoria (reformulació), que no rep
    cap vot, retorna un rànquing buit amb `best_model=None` — no ha de
    ser mai contaminada per les altres.

    Configuració:
        - Categoria A = correcció, gemma guanya al 60%.
        - Categoria B = traducció, qwen guanya al 60%.
        - Categoria C = reformulació, cap vot.
        - Els 1600 vots totals s'alternen entre A i B (800 cadascuna).
        - Els vots de la mateixa parella no es reciclen entre categories:
          els prompts són diferents (`correccio-scale-*` vs `traduccio-scale-*`).

    Comprovacions:
        - `best_model` de correcció és gemma; de traducció és qwen.
        - Els skills BT ho confirmen a cada categoria.
        - Els dos rànquings són estables.
        - Els recomptes de vots són els que hem plantat a cada categoria.
        - Reformulació retorna una estructura buida coherent.
    """
    _assert_running_against_test_database(session)
    _seed_prompts_with_responses(session, "correccio", n_prompts=5)
    _seed_prompts_with_responses(session, "traduccio", n_prompts=5)

    plan = {
        "correccio": "gemma-3-4b-it",
        "traduccio": "qwen-3.5-9b",
    }
    win_prob = 0.60
    # 5 prompts × 3 parelles per categoria = 15 cel·les. 800 vots/categoria →
    # ~53 vots/cel·la; suficient perquè la CI del gap del guanyador a
    # cada categoria quedi clarament positiva amb només 5 clusters.
    n_votes_per_category = 800
    n_votes_total = n_votes_per_category * len(plan)

    # Ni el RNG ens atura. Som i serem
    rng = np.random.default_rng(seed=1714)

    categories_cycle = list(plan.keys())

    for i in range(n_votes_total):
        # Alternem categories a cada iteració; així cap `select_next_task` no rep
        # cap ajuda estranya per haver-se cridat repetidament sobre la mateixa BD.
        category_code = categories_cycle[i % len(categories_cycle)]
        favored_model = plan[category_code]

        task = select_next_task(session, category_code, seed=i)
        assert task is not None, (
            f"Sampler ha retornat None a la iteració {i} (categoria={category_code})"
        )

        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])

        winner = _planted_winner(rng, response_a.model, response_b.model, favored_model, win_prob)
        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            session_id=f"cross-cat-{i % 30}",
        )

    # 1) Cada categoria ha rebut exactament els vots que li tocaven.
    for category_code in plan:
        ranking = compute_ranking(session, category_code)
        assert ranking["n_votes_total"] == n_votes_per_category, (
            f"La categoria {category_code} té {ranking['n_votes_total']} vots "
            f"(esperats {n_votes_per_category}). Possible fuga entre categories."
        )

    # 2) Cada categoria identifica el seu propi guanyador; els guanyadors són
    #    DIFERENTS (si es barregessin, tots dos serien iguals).
    ranking_correccio = compute_ranking(session, "correccio")
    ranking_traduccio = compute_ranking(session, "traduccio")

    assert ranking_correccio["best_model"] == plan["correccio"], (
        f"Correcció: esperat {plan['correccio']}, obtingut {ranking_correccio['best_model']}"
    )
    assert ranking_traduccio["best_model"] == plan["traduccio"], (
        f"Traducció: esperat {plan['traduccio']}, obtingut {ranking_traduccio['best_model']}"
    )
    assert ranking_correccio["best_model"] != ranking_traduccio["best_model"], (
        "Els guanyadors haurien de ser diferents; si són iguals, hi ha "
        "contaminació entre categories."
    )

    # 3) A cada categoria, el skill del guanyador supera els dels altres.
    for category_code, ranking in [
        ("correccio", ranking_correccio),
        ("traduccio", ranking_traduccio),
    ]:
        favored = plan[category_code]
        skills = ranking["bt_skills"]
        for other in MODELS:
            if other == favored:
                continue
            assert skills[favored] > skills[other], (
                f"[{category_code}] Skill de {favored} ({skills[favored]}) no supera "
                f"el de {other} ({skills[other]})"
            )

    # 4) Els dos rànquings són estables.
    for category_code in plan:
        confidence = assess_confidence(session, category_code, n_bootstrap=200, seed=1714)
        assert confidence["is_stable"] is True, (
            f"[{category_code}] rànquing inestable amb 400 vots i un guanyador plantat. "
            f"CI=[{confidence['ci_lo']}, {confidence['ci_hi']}]"
        )

    # 5) Reformulació no ha rebut cap vot: retorna una estructura buida coherent
    #    i cap dels vots d'altres categories la contamina.
    ranking_reformulacio = compute_ranking(session, "reformulacio")
    assert ranking_reformulacio["n_votes_total"] == 0
    assert ranking_reformulacio["best_model"] is None
    assert ranking_reformulacio["bt_skills"] == {}
    assert ranking_reformulacio["raw_pairwise"] == []


# ---------------------------------------------------------------------------
# Test 5 — un nou model apareix a mig camí i el sistema s'hi adapta.
# ---------------------------------------------------------------------------


def test_ranking_adapts_when_new_model_added_mid_campaign(session):
    """Fase 1: gemma guanya contra els altres 2. Fase 2: apareix un 4t model dominant.

    A Fita 2 se n'incorporaran més models sense buidar la BD. Aquest test
    verifica que:
        - `compute_ranking` descobreix dinàmicament els models que apareixen
          a la BD (no és una llista fixa al codi).
        - El sampler comença a servir cel·les amb el nou model tan bon punt
          hi ha respostes disponibles.
        - `assess_confidence` gestiona 4 models sense trencar-se.
        - Les votacions prèvies no queden corrompudes; només s'afegeix
          informació nova.

    Configuració:
        - Categoria: correcció.
        - 5 prompts × 3 models = 15 respostes inicials.
        - Fase 1 (vots 1..300): gemma guanya al 60% quan és a la parella,
          els altres 50/50. Snapshot: gemma és la millor amb 3 models.
        - Introduïm un 4t model, `another-model-4b`, amb resposta a cadascun
          dels 5 prompts.
        - Fase 2 (vots 301..800): el nou model guanya al 70% contra qualsevol.
          Snapshot final: el nou model és el millor amb 4 models.

    Comprovacions:
        - Snapshot Fase 1: 3 models al rànquing, gemma és la millor.
        - Post-Fase 2: 4 models al rànquing, `another-model-4b` és la millor,
          gemma queda com a segona.
        - El total de vots és 800 exactes.
        - El rànquing final és estable.
    """
    _assert_running_against_test_database(session)
    seeded = _seed_prompts_with_responses(session, "correccio", n_prompts=5)
    n_votes_phase1 = 300
    n_votes_phase2 = 500
    win_prob_gemma = 0.60
    win_prob_newcomer = 0.70
    newcomer = "another-model-4b"

    # Ni el RNG ens atura. Som i serem
    rng = np.random.default_rng(seed=1714)

    # ---- Fase 1: només els 3 models inicials ----
    for i in range(n_votes_phase1):
        task = select_next_task(session, "correccio", seed=i)
        assert task is not None, f"Fase 1 iteració {i}: sampler ha retornat None"

        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])
        winner = _planted_winner(
            rng, response_a.model, response_b.model, "gemma-3-4b-it", win_prob_gemma
        )
        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            session_id=f"phase1-{i % 20}",
        )

    # Snapshot Fase 1: gemma és la millor amb només 3 models al mapa.
    snapshot = compute_ranking(session, "correccio")
    assert snapshot["best_model"] == "gemma-3-4b-it"
    assert set(snapshot["models"]) == set(MODELS)
    assert snapshot["n_votes_total"] == n_votes_phase1

    # ---- Afegim un 4t model amb resposta a cada prompt ----
    for prompt, responses in seeded:
        new_response = Response(
            prompt=prompt, model=newcomer, text=f"Resposta de {newcomer} a {prompt.code}"
        )
        session.add(new_response)
        responses[newcomer] = new_response
    session.flush()

    # ---- Fase 2: el nou model guanya al 70% contra qualsevol ----
    for j in range(n_votes_phase2):
        i = n_votes_phase1 + j
        task = select_next_task(session, "correccio", seed=i)
        assert task is not None, f"Fase 2 iteració {j}: sampler ha retornat None"

        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])
        winner = _planted_winner(
            rng, response_a.model, response_b.model, newcomer, win_prob_newcomer
        )
        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            session_id=f"phase2-{j % 20}",
        )

    # ---- Snapshot final ----
    final = compute_ranking(session, "correccio")
    confidence = assess_confidence(session, "correccio", n_bootstrap=200, seed=1714)

    # 1) Ara hi ha 4 models al rànquing, descoberts dinàmicament.
    assert set(final["models"]) == set(MODELS) | {newcomer}, (
        f"Esperats 4 models, obtinguts {final['models']}"
    )

    # 2) El nou model és ara el millor: encara que gemma tingués un cap de sortida,
    #    500 vots amb un avantatge del 70% són suficients per superar-la.
    assert final["best_model"] == newcomer, (
        f"Esperat {newcomer} com a millor, obtingut {final['best_model']}. "
        f"Skills: {final['bt_skills']}"
    )

    # 3) gemma segueix superant els altres dos originals (era la segona millor).
    skills = final["bt_skills"]
    for other in ["qwen-3.5-9b", "salamandra-7b-instruct"]:
        assert skills["gemma-3-4b-it"] > skills[other], (
            f"gemma ha quedat per sota de {other} després de la Fase 2. Skills: {skills}"
        )

    # 4) Total de vots exacte: no s'han duplicat ni perdut.
    assert final["n_votes_total"] == n_votes_phase1 + n_votes_phase2

    # 5) El rànquing amb 4 models és estable — assess_confidence no falla amb n>3.
    assert confidence["is_stable"] is True, (
        f"Rànquing inestable després d'introduir el nou model. "
        f"CI=[{confidence['ci_lo']}, {confidence['ci_hi']}]"
    )


# ---------------------------------------------------------------------------
# Test 6 — nous prompts a mig camí; el sistema els incorpora sense trencar-se.
# ---------------------------------------------------------------------------


def test_ranking_adapts_when_new_prompts_added_mid_campaign(session):
    """Fase 1: rànquing amb 3 prompts. Fase 2: afegim 2 prompts més i seguim.

    Escenari real: els lingüistes lliuren més prompts a mig camí. La BD no
    es buida. Comprovem que:
        - El sampler descobreix els nous prompts i comença a servir-los.
        - Quota-balanced els prioritza (comencen a 0 vots).
        - `compute_ranking` continua funcionant.
        - `assess_confidence.n_prompts` reflecteix el nombre total de prompts
          que han rebut vots (5 al final).
        - El guanyador plantat es manté a través de la transició.

    Configuració:
        - Categoria: traducció.
        - Fase 1: 3 prompts × 3 parelles = 9 cel·les.
          300 vots amb gemma al 60%.
        - Afegim 2 prompts (arribem a 5 prompts × 3 parelles = 15 cel·les).
        - Fase 2: 500 vots addicionals; gemma continua guanyant al 60%.

    Comprovacions:
        - Snapshot Fase 1: 3 prompts amb vots, gemma és la millor.
        - Post-Fase 2: 5 prompts amb vots, gemma continua sent la millor,
          els 2 nous prompts han rebut vots reals gràcies al quota-balanced,
          el rànquing final és estable.
    """
    _assert_running_against_test_database(session)
    _seed_prompts_with_responses(session, "traduccio", n_prompts=3)
    n_votes_phase1 = 300
    n_votes_phase2 = 500
    favored_model = "gemma-3-4b-it"
    win_prob = 0.60

    # Ni el RNG ens atura. Som i serem
    rng = np.random.default_rng(seed=1714)

    # ---- Fase 1 amb 3 prompts ----
    for i in range(n_votes_phase1):
        task = select_next_task(session, "traduccio", seed=i)
        assert task is not None, f"Fase 1 iteració {i}: sampler ha retornat None"

        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])
        winner = _planted_winner(rng, response_a.model, response_b.model, favored_model, win_prob)
        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            session_id=f"phase1-prompts-{i % 20}",
        )

    # Snapshot Fase 1: només 3 prompts al rànquing.
    snap_ranking = compute_ranking(session, "traduccio")
    snap_confidence = assess_confidence(session, "traduccio", n_bootstrap=200, seed=1714)
    assert snap_confidence["n_prompts"] == 3, (
        f"Esperats 3 prompts amb vots a la Fase 1, obtinguts {snap_confidence['n_prompts']}"
    )
    assert snap_ranking["best_model"] == favored_model
    assert snap_ranking["n_votes_total"] == n_votes_phase1

    # ---- Afegim 2 prompts nous a la categoria (arribem a 5 prompts totals) ----
    cat_traduccio = _category(session, "traduccio")
    for i in range(3, 5):
        prompt = Prompt(
            version="vtest",
            code=f"traduccio-scale-{i:02d}",
            category_id=cat_traduccio.id,
            text=f"Prompt {i} a traducció (afegit a mig camí)",
        )
        session.add(prompt)
        session.flush()
        for m in MODELS:
            session.add(Response(prompt=prompt, model=m, text=f"Resposta de {m} a {prompt.code}"))
        session.flush()

    # ---- Fase 2 amb 5 prompts ----
    for j in range(n_votes_phase2):
        i = n_votes_phase1 + j
        task = select_next_task(session, "traduccio", seed=i)
        assert task is not None, f"Fase 2 iteració {j}: sampler ha retornat None"

        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])
        winner = _planted_winner(rng, response_a.model, response_b.model, favored_model, win_prob)
        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            session_id=f"phase2-prompts-{j % 20}",
        )

    # ---- Snapshot final ----
    final_ranking = compute_ranking(session, "traduccio")
    final_confidence = assess_confidence(session, "traduccio", n_bootstrap=200, seed=1714)

    # 1) Ara tots 5 prompts tenen vots (`n_prompts` compta prompts amb vots decisius).
    assert final_confidence["n_prompts"] == 5, (
        f"Esperats 5 prompts amb vots després de la Fase 2, "
        f"obtinguts {final_confidence['n_prompts']}"
    )

    # 2) Els prompts NOUS han rebut vots reals: el quota-balanced els ha prioritzat
    #    perquè començaven a 0 vots quan la resta ja n'acumulava desenes.
    #    Comprovem-ho llegint els vots per prompt directament de la BD.
    new_prompt_codes = ["traduccio-scale-03", "traduccio-scale-04"]
    for code in new_prompt_codes:
        prompt = session.scalar(select(Prompt).where(Prompt.code == code))
        assert prompt is not None
        n_votes_this_prompt = session.scalar(
            select(Vote).where(Vote.prompt_id == prompt.id).exists().select()
        )
        assert n_votes_this_prompt is True, (
            f"El prompt nou {code} no ha rebut cap vot; quota-balanced no l'ha prioritzat"
        )

    # 3) gemma continua sent la millor: la transició no altera el guanyador plantat.
    assert final_ranking["best_model"] == favored_model, (
        f"Esperat {favored_model}, obtingut {final_ranking['best_model']}. "
        f"Skills: {final_ranking['bt_skills']}"
    )

    # 4) Total exacte de vots.
    assert final_ranking["n_votes_total"] == n_votes_phase1 + n_votes_phase2

    # 5) El rànquing final és estable.
    assert final_confidence["is_stable"] is True, (
        f"Rànquing inestable després d'afegir prompts. "
        f"CI=[{final_confidence['ci_lo']}, {final_confidence['ci_hi']}]"
    )


# ---------------------------------------------------------------------------
# Test 7 — apareix una nova categoria a mig camí; el sistema s'hi adapta.
# ---------------------------------------------------------------------------


def test_new_category_added_mid_campaign_is_independent(session):
    """Afegim una nova categoria (`cultura`) després d'haver votat a `correccio`.

    Escenari real: el projecte decideix afegir una categoria nova (per exemple,
    "cultura" — no seedejada al `conftest`). El sistema ha de:
        - Retornar None si es demana una tasca d'una categoria sense prompts.
        - Un cop hi ha prompts a la nova categoria, servir-los amb normalitat.
        - Mantenir els rànquings de les categories velles intactes.
        - Calcular un rànquing independent per a la nova categoria.
        - No contaminar les altres categories existents (`reformulacio`
          continua buida).

    Configuració:
        - Fase 1: 5 prompts × 3 models a `correccio`. 1800 vots amb gemma
          al 60% (~120 vots/cel·la per obtenir un rànquing clarament estable).
        - Comprovem: sampling de `cultura` retorna None (encara no té prompts).
        - Creem la categoria `cultura` (no és al catàleg YAML) i
          afegim 3 prompts × 3 respostes.
        - Fase 2: 800 vots a `cultura` amb salamandra al 60% (3 prompts ×
          3 parelles = 9 cel·les → ~89 vots/cel·la, prou generós).

    Comprovacions finals:
        - `correccio` conserva els seus 1800 vots i gemma com a millor model.
        - `cultura` té 800 vots i salamandra com a millor model.
        - Els guanyadors són diferents (no hi ha fuga entre categories).
        - Ambdós rànquings són estables.
        - `reformulacio` continua buida.
    """
    _assert_running_against_test_database(session)
    _seed_prompts_with_responses(session, "correccio", n_prompts=5)
    # Fase 1 (correcció, 5 prompts × 3 parelles = 15 cel·les): 1800 vots per
    # tenir ~120 vots/cel·la i que la CI clusteritzada del gap sigui clarament
    # positiva amb només 5 clusters — mateixa lliçó que Test 1 i Test 4.
    n_votes_phase1 = 1800
    n_votes_phase2 = 800
    win_prob = 0.60
    correccio_winner = "gemma-3-4b-it"
    cultura_winner = "salamandra-7b-instruct"

    # Ni el RNG ens atura. Som i serem
    rng = np.random.default_rng(seed=1714)

    # ---- Fase 1: només `correccio` existeix com a categoria amb prompts ----
    for i in range(n_votes_phase1):
        task = select_next_task(session, "correccio", seed=i)
        assert task is not None, f"Fase 1 iteració {i}: sampler ha retornat None"

        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])
        winner = _planted_winner(
            rng, response_a.model, response_b.model, correccio_winner, win_prob
        )
        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            session_id=f"phase1-cat-{i % 20}",
        )

    # Snapshot Fase 1: correccio identifica gemma com a millor.
    snap_correccio = compute_ranking(session, "correccio")
    assert snap_correccio["best_model"] == correccio_winner
    assert snap_correccio["n_votes_total"] == n_votes_phase1

    # ---- Verifiquem que una categoria SENSE prompts retorna None al sampler ----
    # (`cultura` encara no existeix a la BD; el sampler ha de gestionar-ho
    # amb elegància.)
    task_empty = select_next_task(session, "cultura", seed=42)
    assert task_empty is None, (
        "Sampler ha retornat una tasca per a una categoria sense prompts. Hauria de retornar None."
    )

    # ---- Creem la categoria `cultura` i li afegim 3 prompts × 3 respostes ----
    session.add(
        Category(
            code="cultura",
            name="Cultura",
            description="Preguntes sobre cultura catalana.",
        )
    )
    session.flush()
    _seed_prompts_with_responses(session, "cultura", n_prompts=3)

    # ---- Fase 2: votem a `cultura` amb salamandra com a guanyadora plantada ----
    for j in range(n_votes_phase2):
        i = n_votes_phase1 + j
        task = select_next_task(session, "cultura", seed=i)
        assert task is not None, f"Fase 2 iteració {j}: sampler ha retornat None"

        response_a = session.get(Response, task["response_a_id"])
        response_b = session.get(Response, task["response_b_id"])
        winner = _planted_winner(rng, response_a.model, response_b.model, cultura_winner, win_prob)
        _record_vote(
            session,
            prompt_id=task["prompt_id"],
            response_a_id=task["response_a_id"],
            response_b_id=task["response_b_id"],
            winner=winner,
            session_id=f"phase2-cat-{j % 20}",
        )

    # ---- Comprovacions finals ----
    final_correccio = compute_ranking(session, "correccio")
    final_cultura = compute_ranking(session, "cultura")
    final_reformulacio = compute_ranking(session, "reformulacio")

    # 1) correccio no s'ha alterat: mateixos vots, mateix guanyador.
    assert final_correccio["n_votes_total"] == n_votes_phase1, (
        f"correcció ha canviat el recompte de vots: {final_correccio['n_votes_total']}"
    )
    assert final_correccio["best_model"] == correccio_winner, (
        f"correcció ha canviat de guanyador: {final_correccio['best_model']}"
    )

    # 2) cultura identifica salamandra com a millor.
    assert final_cultura["n_votes_total"] == n_votes_phase2
    assert final_cultura["best_model"] == cultura_winner, (
        f"Esperat {cultura_winner} a cultura, obtingut {final_cultura['best_model']}. "
        f"Skills: {final_cultura['bt_skills']}"
    )

    # 3) Els guanyadors de les dues categories són diferents (sense fuga).
    assert final_correccio["best_model"] != final_cultura["best_model"]

    # 4) Ambdós rànquings són estables.
    conf_correccio = assess_confidence(session, "correccio", n_bootstrap=200, seed=1714)
    conf_cultura = assess_confidence(session, "cultura", n_bootstrap=200, seed=1714)
    assert conf_correccio["is_stable"] is True, (
        f"correcció inestable després d'introduir cultura. "
        f"CI=[{conf_correccio['ci_lo']}, {conf_correccio['ci_hi']}]"
    )
    assert conf_cultura["is_stable"] is True, (
        f"cultura inestable. CI=[{conf_cultura['ci_lo']}, {conf_cultura['ci_hi']}]"
    )

    # 5) reformulacio continua buida — les altres categories no la contaminen.
    assert final_reformulacio["n_votes_total"] == 0
    assert final_reformulacio["best_model"] is None
    assert final_reformulacio["bt_skills"] == {}
    assert final_reformulacio["raw_pairwise"] == []
