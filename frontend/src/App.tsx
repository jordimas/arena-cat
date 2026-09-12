import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";

import { api, UNAUTHENTICATED_EVENT } from "./api";
import logotip from "./assets/softcatala-logotip.png";
import Login from "./components/Login";
import RankingView from "./components/RankingView";
import TaskView from "./components/TaskView";
import { clearTask } from "./taskStore";
import type { Category, SessionState } from "./types";

const UNKNOWN: SessionState = { authenticated: false, email: null, email_verified: false };

export default function App() {
  // `null` mentre no sabem si hi ha sessió: sense aquest estat intermedi
  // ensenyaríem el formulari un instant a qui ja té la sessió oberta.
  const [session, setSession] = useState<SessionState | null>(null);
  const [categories, setCategories] = useState<Category[] | null>(null);
  const [categoriesError, setCategoriesError] = useState(false);
  const navigate = useNavigate();

  const refresh = useCallback(async () => {
    try {
      setSession(await api.session());
    } catch {
      // Backend inaccessible: el tractem com a no autenticat, però l'error de
      // connexió ja el mostrarà el formulari quan s'intenti entrar.
      setSession(UNKNOWN);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const refreshCategories = useCallback(async () => {
    setCategoriesError(false);
    try {
      setCategories(await api.categories());
    } catch {
      setCategories(null);
      setCategoriesError(true);
    }
  }, []);

  useEffect(() => {
    void refreshCategories();
  }, [refreshCategories]);

  // La tasca en curs pertany a la sessió: quan s'acaba, s'ha de descartar. Si no,
  // en tornar a entrar es restauraria amb el `vote_after` ja vençut (i per tant
  // sense compte enrere), i si hi entrés una altra persona veuria una tasca que
  // no és seva i que el backend li rebutjaria amb un 403.
  useEffect(() => {
    const onUnauthenticated = () => {
      clearTask();
      setSession(UNKNOWN);
    };
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated);
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated);
  }, []);

  function logout() {
    clearTask();
    void api.logout().finally(() => {
      setSession(UNKNOWN);
      navigate("/login");
    });
  }

  return (
    // El coixí inferior va aquí i no dins de TaskView perquè la barra de vot, que al
    // mòbil és fixada, tapa el final del document — i el final del document és el peu.
    <div className="min-h-screen bg-slate-50 pb-44 text-slate-900 md:pb-0">
      <header className="border-b-4 border-brand-500 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
          <div>
            <h1 className="text-lg font-bold text-brand-600">Arena Cat</h1>
            <p className="text-sm text-slate-500">Avaluació humana de models d'IA en català</p>
          </div>
          {session?.authenticated && (
            <div className="flex items-center gap-2">
              <span className="hidden text-sm text-slate-500 sm:inline">{session.email}</span>
              {/* Icona sola: `title` per al ratolí i `aria-label` per al lector de
                  pantalla, que altrament només trobaria un botó sense nom. */}
              <button
                type="button"
                onClick={logout}
                title="Tanca la sessió"
                aria-label="Tanca la sessió"
                className="rounded-md p-2 text-slate-500 hover:bg-brand-100 hover:text-brand-600 focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:outline-none"
              >
                <LogoutIcon />
              </button>
            </div>
          )}
        </div>
      </header>

      <main>
        {session === null ? (
          <p className="px-4 py-10 text-center text-slate-500">Carregant…</p>
        ) : (
          <Routes>
            <Route
              path="/login"
              element={
                session.authenticated ? <Navigate to="/" replace /> : <Login onLoggedIn={refresh} />
              }
            />
            <Route
              path="/"
              element={
                session.authenticated ? (
                  categories ? (
                    <TaskView categories={categories} />
                  ) : (
                    <CategoriesStatus error={categoriesError} onRetry={refreshCategories} />
                  )
                ) : categories ? (
                  <RankingView categories={categories} onLogin={() => navigate("/login")} />
                ) : (
                  <CategoriesStatus error={categoriesError} onRetry={refreshCategories} />
                )
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl flex-col items-center gap-2 px-4 py-6">
          <span className="text-sm text-slate-500">Un projecte de</span>
          {/* El logotip és el nom escrit, així que l'`alt` és el nom i no el
              repetim al costat. `width`/`height` eviten que el peu salti mentre
              la imatge es carrega. */}
          <a
            href="https://www.softcatala.org"
            target="_blank"
            rel="noreferrer"
            className="rounded focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:outline-none"
          >
            <img src={logotip} alt="Softcatalà" width={146} height={90} className="h-14 w-auto" />
          </a>
        </div>
      </footer>
    </div>
  );
}

function CategoriesStatus({ error, onRetry }: { error: boolean; onRetry: () => void }) {
  return (
    <div className="px-4 py-10 text-center text-slate-500">
      {error ? (
        <>
          <p role="alert">No s'han pogut carregar les categories.</p>
          <button type="button" onClick={onRetry} className="mt-3 underline hover:text-brand-600">
            Torna-ho a provar
          </button>
        </>
      ) : (
        <p>Carregant categories…</p>
      )}
    </div>
  );
}

/** Porta amb una fletxa cap enfora: el gest habitual per a «surt». */
function LogoutIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-5 w-5"
      aria-hidden="true"
    >
      <path d="M9 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}
