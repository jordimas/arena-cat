import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "../api";
import { splitPrompt } from "../diff";
import { clearTask, loadSavedTask, saveTask, secondsUntilVote } from "../taskStore";
import type { Category, CategoryFilter, Progress, Task, Winner } from "../types";
import Onboarding, { type OnboardingStep } from "./Onboarding";
import ProgressBar from "./ProgressBar";
import ResponseCard from "./ResponseCard";

const VOTE_OPTIONS: {
  winner: Winner;
  label: string;
  shortcut: string;
}[] = [
  { winner: "a", label: "A és millor", shortcut: "a" },
  { winner: "b", label: "B és millor", shortcut: "b" },
  { winner: "tie", label: "Empat", shortcut: "e" },
  { winner: "neither", label: "Cap de les dues", shortcut: "c" },
];

export default function TaskView({ categories }: { categories: Category[] }) {
  const [category, setCategory] = useState<CategoryFilter>("");
  const [task, setTask] = useState<Task | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [remaining, setRemaining] = useState(0);
  /** No queden tasques per al filtre actual (el backend ha respost 404). */
  const [exhausted, setExhausted] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const headerRef = useRef<HTMLElement | null>(null);
  const originalTextRef = useRef<HTMLElement | null>(null);
  const responsesRef = useRef<HTMLDivElement | null>(null);
  const voteRef = useRef<HTMLDivElement | null>(null);

  const loadProgress = useCallback(async () => {
    // El progrés és informatiu: si falla, no bloquegem l'avaluació.
    try {
      setProgress(await api.progress());
    } catch {
      setProgress(null);
    }
  }, []);

  /** Mostra una tasca i en calcula l'espera pel `vote_after` que porta el token. */
  const showTask = useCallback((next: Task) => {
    setTask(next);
    setRemaining(secondsUntilVote(next.token));
  }, []);

  // Si es canvia de categoria abans que respongui la petició anterior, les
  // respostes poden arribar desordenades. Només apliquem la de la petició més
  // recent; les altres arriben tard i les ignorem.
  const requestId = useRef(0);

  const loadTask = useCallback(
    async (code: CategoryFilter, { restore = false } = {}) => {
      // En recarregar recuperem la tasca que s'estava llegint. Si el filtre ha
      // canviat, en volem una de nova encara que n'hi hagi de desada.
      if (restore) {
        const saved = loadSavedTask();
        if (saved && (code === "" || saved.category_code === code)) {
          showTask(saved);
          return;
        }
      }

      const id = ++requestId.current;
      setBusy(true);
      setMessage(null);
      setExhausted(false);
      setTask(null);
      try {
        const next = await api.nextTask(code);
        if (id !== requestId.current) return;
        saveTask(next);
        showTask(next);
      } catch (err) {
        if (id !== requestId.current) return;
        clearTask();
        setTask(null);
        // El 404 no és cap error: vol dir que no queda res per avaluar aquí. El
        // backend fa servir el mateix missatge tant si s'ha acabat la categoria
        // com si s'ha acabat tot, així que ho distingim amb el progrés.
        if (err instanceof ApiError && err.status === 404) {
          setExhausted(true);
          await loadProgress();
        } else {
          setMessage(err instanceof ApiError ? err.message : "Error de connexió");
        }
      } finally {
        if (id === requestId.current) setBusy(false);
      }
    },
    [showTask, loadProgress],
  );

  // Recordem per a quina categoria hem carregat perquè l'efecte sigui idempotent:
  // en desenvolupament `StrictMode` l'executa dues vegades, i sense aquesta guarda
  // la segona passada demanaria una tasca nova i descartaria la que acabem de posar.
  const loadedCategory = useRef<CategoryFilter | null>(null);
  useEffect(() => {
    if (loadedCategory.current === category) return;

    const isFirstLoad = loadedCategory.current === null;
    loadedCategory.current = category;

    // Si la tasca que ja es mostra satisfà el filtre nou, ens la quedem: canviar-la
    // faria rellegir dos textos llargs i reiniciaria l'espera sense cap motiu.
    if (task && (category === "" || task.category_code === category)) return;

    void loadTask(category, { restore: isFirstLoad });
  }, [category, task, loadTask]);

  useEffect(() => {
    void loadProgress();
  }, [loadProgress]);

  // Compte enrere que desbloqueja els botons. Sense això el backend retorna 425.
  useEffect(() => {
    if (remaining <= 0) return;
    const timer = setTimeout(() => setRemaining((value) => value - 1), 1000);
    return () => clearTimeout(timer);
  }, [remaining]);

  /** Envia el vot o l'omissió i encadena la tasca següent. */
  const resolve = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await action();
        // Ja s'ha votat o omès: la tasca desada ha deixat de ser vàlida.
        clearTask();
        await loadTask(category);
        await loadProgress();
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          // El token de la tasca ha caducat o no és vàlid: mai tornarà a
          // funcionar, així que en carreguem una de nova en lloc de deixar
          // l'usuari encallat repetint el mateix error indefinidament.
          clearTask();
          await loadTask(category);
          setMessage("El token d'aquesta tasca ha caducat.");
          return;
        }
        setMessage(err instanceof ApiError ? err.message : "Error de connexió");
        setBusy(false);
      }
    },
    [category, loadTask, loadProgress],
  );

  const locked = remaining > 0 || busy || !task;

  // Dreceres de teclat: cada avaluador fa 90 vots o més, i obligar-lo a moure el
  // ratolí fins al botó a cada tasca és una fricció que se suma 90 vegades.
  useEffect(() => {
    // Sense aquesta guarda, votar amb tecles funcionaria per sota del
    // tutorial mentre és obert, sense que la persona ho vegi.
    if (locked || !task || showOnboarding) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

      const option = VOTE_OPTIONS.find((item) => item.shortcut === event.key.toLowerCase());
      if (!option) return;

      event.preventDefault();
      void resolve(() => api.vote(task!.token, option.winner));
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [locked, task, resolve, showOnboarding]);

  const parts = task ? splitPrompt(task.prompt) : null;
  const taskCategory = categories.find((item) => item.code === task?.category_code);
  const isCorrection = task?.category_code === "correccio";

  const onboardingSteps: OnboardingStep[] = [
    {
      ref: headerRef,
      title: "Què se li ha demanat al model",
      text: "Aquí veus la categoria de la tasca i la instrucció exacta que s'ha enviat als dos models.",
    },
    ...(parts?.source
      ? [
          {
            ref: originalTextRef,
            title: "Text original",
            text: "Aquest és el text de partida, tal com el va rebre el model, sense cap correcció.",
          },
        ]
      : []),
    {
      ref: responsesRef,
      title: "Compara les dues respostes",
      text: "Llegeix la resposta A i la B. No saps quin model ha generat cadascuna: és una avaluació cega.",
    },
    {
      ref: voteRef,
      title: "Vota",
      text: "Tria quina resposta és millor, si estan empatades o si cap de les dues és bona. També pots fer servir les dreceres A, B, E i C.",
    },
  ];

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-2xl font-bold text-brand-600">Avaluació de respostes</h2>

        {/* Discret a propòsit: aquesta pantalla es repeteix 90+ vegades per
            avaluador, i el tutorial només cal la primera vegada. Al costat del
            filtre no hi cabia al mòbil: hi xocava amb el «pendents», que no es
            pot encongir perquè és `whitespace-nowrap`. */}
        {task && (
          <button
            type="button"
            onClick={() => setShowOnboarding(true)}
            className="shrink-0 text-sm text-slate-500 underline hover:text-brand-600"
          >
            Tutorial
          </button>
        )}
      </div>

      {/* Filtre i progrés comparteixen línia: dues files senceres abans de la tasca
          empenyien massa avall el text, que és el que s'ha de llegir. El rètol
          «Categoria» desapareix perquè el desplegable ja diu què fa. */}
      <div className="mb-5 flex items-center gap-3 sm:gap-20">
        <select
          aria-label="Categoria"
          value={category}
          onChange={(event) => setCategory(event.target.value as CategoryFilter)}
          // `text-base` també al mòbil: amb menys de 16 px, Safari d'iOS fa zoom
          // automàtic en tocar el desplegable i deixa la pàgina desquadrada.
          className="shrink-0 rounded-md border border-slate-300 px-3 py-2 text-base sm:w-56"
        >
          <option value="">Qualsevol categoria</option>
          {categories.map((item) => (
            <option key={item.code} value={item.code}>
              {item.name}
            </option>
          ))}
        </select>

        {progress && <ProgressBar progress={progress} className="flex-1" />}
      </div>

      {message && (
        <p role="status" className="rounded-md bg-amber-50 px-4 py-3 text-amber-900">
          {message}
        </p>
      )}

      {exhausted && (
        <Exhausted
          category={category}
          categories={categories}
          progress={progress}
          onContinue={() => setCategory("")}
        />
      )}

      {task && parts && (
        <>
          <section ref={headerRef} className="mb-4">
            {/* Sense aquestes frases, algú nou podia confondre l'enunciat de sota
                amb instruccions per a ell mateix, quan en realitat és el que es va
                demanar als dos models (Jordi, issue #56). Reutilitzem l'estil de
                «Text original» (mb-1 text-sm font-semibold tracking-wide uppercase)
                perquè els rètols de la pàgina segueixin un sol llenguatge visual. */}
            <hr className="mb-4 border-slate-200" />
            <p className="mb-1 text-sm">
              <span className="font-semibold tracking-wide text-slate-500 uppercase">
                Tipus de tasca:
              </span>{" "}
              <span className="text-base font-semibold text-slate-700">
                {taskCategory?.name ?? task.category_code}
              </span>
            </p>
            <h3 className="mb-1 text-sm font-semibold tracking-wide text-slate-500 uppercase">
              Indicació enviada al model
            </h3>
            <code className="text-base font-semibold text-slate-700">«{parts.instruction}»</code>
          </section>

          {/* A correcció el text es pot plegar: el diff de cada targeta ja el conté, i
              plegat es guanya molt d'espai en textos llargs. Però obert per defecte,
              perquè llegir l'original net és més fàcil que reconstruir-lo del diff. */}
          {parts.source &&
            (isCorrection ? (
              <details
                ref={(el) => {
                  originalTextRef.current = el;
                }}
                open
                className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3"
              >
                <summary className="cursor-pointer text-sm font-semibold tracking-wide text-slate-500 uppercase">
                  Text original
                </summary>
                <p className="mt-2 leading-relaxed whitespace-pre-wrap text-slate-800">
                  {parts.source}
                </p>
              </details>
            ) : (
              <section
                ref={originalTextRef}
                className="mb-6 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3"
              >
                <h3 className="mb-1 text-sm font-semibold tracking-wide text-slate-500 uppercase">
                  Text original
                </h3>
                <p className="leading-relaxed whitespace-pre-wrap text-slate-800">{parts.source}</p>
              </section>
            ))}

          <div className="mb-3">
            <p className="mb-1 text-sm">
              <span className="font-semibold tracking-wide text-slate-500 uppercase">
                La vostra tasca:
              </span>{" "}
              <span className="text-base font-semibold text-slate-700">
                avaluar la resposta dels dos models.
              </span>
            </p>
            {taskCategory?.evaluation_instructions && (
              <>
                <p className="mt-3 mb-1 text-sm font-semibold tracking-wide text-slate-500 uppercase">
                  A tenir en compte:
                </p>
                <p className="text-base leading-relaxed whitespace-pre-line text-slate-700">
                  {taskCategory.evaluation_instructions}
                </p>
              </>
            )}
          </div>

          {isCorrection && <DiffLegend />}

          {/* Apilades al mòbil, en dues columnes a partir de pantalla mitjana. */}
          <div ref={responsesRef} className="mb-6 grid gap-4 md:grid-cols-2">
            <ResponseCard
              label="Resposta A"
              text={task.response_a}
              source={isCorrection ? parts.source : undefined}
            />
            <ResponseCard
              label="Resposta B"
              text={task.response_b}
              source={isCorrection ? parts.source : undefined}
            />
          </div>

          {/* Al mòbil la pàgina fa unes tres pantalles: amb els botons al final del
              document, votar obligaria a baixar del tot cada vegada. Fixats a baix
              són sempre a l'abast del polze. A partir de `md` tornen al flux normal. */}
          <div className="fixed inset-x-0 bottom-0 z-10 border-t border-slate-200 bg-white/95 px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur md:static md:border-0 md:bg-transparent md:p-0 md:backdrop-blur-none">
            <div className="mx-auto max-w-5xl">
              <div ref={voteRef} className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                {VOTE_OPTIONS.map((option) => (
                  <button
                    key={option.winner}
                    type="button"
                    disabled={locked}
                    onClick={() => void resolve(() => api.vote(task.token, option.winner))}
                    // Els quatre botons tenen el mateix pes visual a propòsit: destacar-ne
                    // algun (p. ex. A i B) podria esbiaixar l'avaluador cap a triar-lo per
                    // la seva prominència, no perquè li sembli realment millor.
                    className="flex items-center justify-center gap-2 rounded-md bg-brand-500 px-3 py-3 font-medium text-white hover:bg-brand-600 disabled:opacity-40"
                  >
                    {option.label}
                    <kbd className="hidden rounded border border-white/40 px-1.5 text-xs lg:inline">
                      {option.shortcut.toUpperCase()}
                    </kbd>
                  </button>
                ))}
              </div>

              <div className="mt-2 flex items-center justify-center gap-3 text-sm text-slate-500">
                {/* El compte enrere no s'anuncia: amb `aria-live` un lector de pantalla
                    llegiria un número nou cada segon. Només anunciem el desbloqueig,
                    que és l'únic canvi que la persona necessita saber. */}
                <span>
                  {remaining > 0 ? `Podràs votar d'aquí ${remaining} s.` : "Ja pots votar."}
                </span>
                <span className="sr-only" role="status">
                  {remaining > 0 ? "" : "Ja pots votar."}
                </span>
                <span aria-hidden="true">·</span>
                {/* Ometre no espera el compte enrere: si no vols jutjar la tasca, no cal llegir-la. */}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void resolve(() => api.skipTask(task.token))}
                  className="underline hover:text-brand-600 disabled:opacity-40"
                >
                  Omet
                </button>
              </div>
            </div>
          </div>

          {showOnboarding && (
            <Onboarding steps={onboardingSteps} onDone={() => setShowOnboarding(false)} />
          )}
        </>
      )}
    </div>
  );
}

/** Final de recorregut: o s'ha acabat la categoria triada, o s'ha acabat tot.
 *
 * El backend respon el mateix 404 en tots dos casos, però el progrés global els
 * distingeix: si encara queden tasques pendents, és que només s'ha exhaurit el
 * filtre actual i n'hi ha a la resta de categories.
 */
function Exhausted({
  category,
  categories,
  progress,
  onContinue,
}: {
  category: CategoryFilter;
  categories: Category[];
  progress: Progress | null;
  onContinue: () => void;
}) {
  const label = categories.find((item) => item.code === category)?.name ?? category;
  // Sense filtre, el backend ja ha mirat totes les categories. Amb filtre, només ho
  // podem afirmar si tenim el progrés: si ha fallat, oferim continuar, que és el
  // camí segur — mai anunciem el final basant-nos en una xifra que no tenim.
  const allDone = category === "" || progress?.remaining === 0;

  return (
    <section className="rounded-lg border border-slate-200 bg-white px-6 py-8 text-center">
      {allDone ? (
        <>
          <h2 className="mb-2 text-xl font-semibold text-brand-600">Ho has avaluat tot</h2>
          <p className="text-slate-600">
            {progress
              ? `Has emès ${progress.voted} vots dels ${progress.total} possibles.`
              : "No queda cap tasca pendent."}
          </p>
          {progress && progress.skipped > 0 && (
            <p className="mt-1 text-sm text-slate-500">
              N'has omès {progress.skipped}, que ja no tornaran a sortir.
            </p>
          )}
          <p className="mt-4 text-sm text-slate-500">
            Gràcies. Quan s'afegeixin models o prompts nous hi tornarà a haver feina.
          </p>
        </>
      ) : (
        <>
          <h2 className="mb-2 text-xl font-semibold text-brand-600">Has completat «{label}»</h2>
          {/* Amb progrés donem la xifra; sense, el botó continua sent útil igualment. */}
          <p className="text-slate-600">
            {progress
              ? `Encara queden ${progress.remaining} tasques a les altres categories.`
              : "Pots continuar amb les altres categories."}
          </p>
          <button
            type="button"
            onClick={onContinue}
            className="mt-4 rounded-md bg-brand-500 px-4 py-2 font-medium text-white hover:bg-brand-600"
          >
            Continua amb la resta
          </button>
        </>
      )}
    </section>
  );
}

/** Sense clau, els colors del diff són endevinalles. */
function DiffLegend() {
  return (
    <p className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-500">
      <span>
        <ins className="rounded bg-emerald-100 px-1 text-emerald-900 no-underline">afegit</ins> pel
        model
      </span>
      <span>
        <del className="rounded bg-brand-100 px-1 text-brand-700">eliminat</del> de l'original
      </span>
    </p>
  );
}
