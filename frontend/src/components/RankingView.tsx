import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import type { Category, CategoryFilter, Ranking } from "../types";

export default function RankingView({
  categories,
  onLogin,
}: {
  categories: Category[];
  onLogin: () => void;
}) {
  const [category, setCategory] = useState<CategoryFilter>("");
  const [ranking, setRanking] = useState<Ranking | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMessage(null);
    // Sense això, en canviar de categoria es veuria un instant la capçalera
    // nova amb la taula encara plena de dades de l'anterior.
    setRanking(null);
    void api
      .ranking(category)
      .then((next) => {
        if (!cancelled) setRanking(next);
      })
      .catch((err) => {
        if (cancelled) return;
        setRanking(null);
        setMessage(err instanceof ApiError ? err.message : "Error de connexió");
      });
    return () => {
      cancelled = true;
    };
  }, [category]);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <h2 className="mb-1 text-2xl font-bold text-slate-900">
        La lliga dels models d'IA en català
      </h2>
      <p className="mb-5 text-slate-500">
        Marcador públic, duels anònims i rànquing recalculat amb cada vot.
      </p>

      <div className="mb-5 inline-flex flex-wrap gap-1 rounded-md border border-slate-200 bg-white p-1">
        <button
          type="button"
          aria-pressed={category === ""}
          onClick={() => setCategory("")}
          className={
            category === ""
              ? "rounded bg-brand-500 px-3 py-1.5 text-sm font-medium text-white"
              : "rounded px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
          }
        >
          Global
        </button>
        {categories.map((item) => (
          <button
            key={item.code}
            type="button"
            aria-pressed={category === item.code}
            onClick={() => setCategory(item.code)}
            className={
              category === item.code
                ? "rounded bg-brand-500 px-3 py-1.5 text-sm font-medium text-white"
                : "rounded px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
            }
          >
            {item.name}
          </button>
        ))}
      </div>

      {message && (
        <p role="status" className="mb-4 rounded-md bg-amber-50 px-4 py-3 text-amber-900">
          {message}
        </p>
      )}

      {ranking && (
        <>
          <section className="mb-4 overflow-hidden rounded-lg border border-slate-200">
            <header className="flex items-center justify-between bg-brand-600 px-5 py-3">
              <div>
                <h3 className="font-semibold text-white">Rànquing en directe</h3>
                <p className="text-sm text-brand-100">
                  {category === ""
                    ? "Global"
                    : (categories.find((item) => item.code === category)?.name ?? category)}
                  {" · "}actualitzat amb els vots de la comunitat
                </p>
              </div>
              {!ranking.confidence.is_stable && (
                <span className="shrink-0 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">
                  Provisional
                </span>
              )}
            </header>

            <table className="w-full text-left">
              <thead>
                <tr className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                  <th className="px-5 py-2">Posició</th>
                  <th className="px-5 py-2">Model</th>
                  <th className="px-5 py-2 text-right">Puntuació</th>
                </tr>
              </thead>
              <tbody>
                {ranking.ranked_models.map((item) => (
                  <tr
                    key={item.model}
                    className={
                      item.model === ranking.best_model
                        ? "border-t border-slate-100 bg-brand-50"
                        : "border-t border-slate-100"
                    }
                  >
                    <td className="px-5 py-3">
                      <span
                        className={
                          item.model === ranking.best_model
                            ? "flex h-7 w-7 items-center justify-center rounded-full bg-brand-500 text-sm font-semibold text-white"
                            : "flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-sm font-semibold text-slate-600"
                        }
                      >
                        {item.rank}
                      </span>
                    </td>
                    <td className="px-5 py-3 font-medium text-slate-800">{item.model}</td>
                    <td className="px-5 py-3 text-right font-mono text-slate-700">
                      {item.bt_skill >= 0 ? "+" : ""}
                      {item.bt_skill.toFixed(2)}
                    </td>
                  </tr>
                ))}
                {ranking.ranked_models.length === 0 && (
                  <tr>
                    <td colSpan={3} className="px-5 py-6 text-center text-slate-500">
                      Encara no hi ha prou vots per calcular el rànquing.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>

            <div className="grid grid-cols-2 gap-4 border-t border-slate-200 px-5 py-4 sm:grid-cols-4">
              <Stat label="Vots" value={ranking.n_votes_total} />
              <Stat label="Decisius" value={ranking.n_votes_decisive} />
              <Stat label="Empats" value={ranking.n_ties} />
              <Stat label="Cap de les dues" value={ranking.n_neither} />
            </div>

            <div className="flex items-center justify-between border-t border-slate-200 px-5 py-3 text-sm">
              <span className="text-slate-500">Confiança del líder</span>
              <span className="font-semibold text-slate-700">
                {Math.round(ranking.confidence.p_best_is_best * 100)}%
              </span>
            </div>
          </section>

          <section className="rounded-lg border border-brand-100 bg-brand-50 px-5 py-4 text-center">
            <p className="mb-3 text-slate-700">
              Fes que el rànquing sigui més fiable: cada comparació ajuda a saber quins models
              responen millor en català.
            </p>
            <button
              type="button"
              onClick={onLogin}
              className="rounded-md bg-brand-500 px-5 py-2 font-medium text-white hover:bg-brand-600"
            >
              Entra o crea un compte per avaluar
            </button>
          </section>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{label}</p>
      <p className="text-lg font-semibold text-slate-800">{value}</p>
    </div>
  );
}
