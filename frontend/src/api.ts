/** Client de l'API d'Arena Cat.
 *
 * Segueix el patró de Garbellaveus: una funció `request` genèrica i un objecte
 * `api` amb un mètode per endpoint.
 *
 * En desenvolupament, `BASE` és relatiu i Vite fa de proxy cap al backend local
 * (vegeu `vite.config.ts`). En producció s'hi posa l'URL del microservei via
 * `VITE_API_BASE_URL`.
 */

import { readDetail, readErrorCode, TASK_TOKEN_INVALID } from "./errors";
import type {
  Category,
  CategoryFilter,
  Progress,
  Ranking,
  SessionState,
  Task,
  Winner,
} from "./types";

// `||` i no `??`: una variable definida però buida (cosa fàcil en un fitxer .env)
// ha de caure igualment al valor per defecte, o les crides perdrien el prefix /api.
const BASE = import.meta.env.VITE_API_BASE_URL || "/api";

/** Error amb el codi HTTP, perquè la interfície pugui distingir 401 de 425 o 409. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** S'emet quan el backend rebutja la sessió, perquè la interfície hi reaccioni. */
export const UNAUTHENTICATED_EVENT = "arena-cat:unauthenticated";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    // Imprescindible: la sessió viatja en una cookie HttpOnly que el backend
    // estableix a /auth/login. Sense això, /task i /vote responen 401.
    credentials: "include",
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);

    // /vote i /task/skip també responen 401 quan el token de tasca (no la
    // sessió) ha caducat; el backend ho marca amb `error_code` perquè no ho
    // confonguem amb una sessió tancada i fem fora algú que encara hi és.
    if (response.status === 401 && readErrorCode(body) !== TASK_TOKEN_INVALID) {
      window.dispatchEvent(new Event(UNAUTHENTICATED_EVENT));
    }

    const detail = readDetail(body);
    throw new ApiError(response.status, detail ?? `Error HTTP ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = {
  categories: async () => (await request<{ categories: Category[] }>("/categories")).categories,

  // Respon 200 tant si hi ha sessió com si no; la cookie és HttpOnly i el client
  // no té cap altra manera de saber-ho.
  session: () => request<SessionState>("/auth/session"),

  // `consent` ha de venir de la persona: el backend en desa la data i la versió a
  // `users`, i enviar-lo sempre cert fabricaria un consentiment que ningú ha donat.
  // La versió no s'envia: el backend la pren de la seva pròpia configuració.
  register: (email: string, password: string, consent: boolean) =>
    request<{ status: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, consent }),
    }),

  login: (email: string, password: string) =>
    request<{ status: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  logout: () => request<{ status: string }>("/auth/logout", { method: "POST" }),

  // Sense `category_code` el backend recorre les categories i serveix la primera
  // que encara tingui feina per a aquest usuari.
  nextTask: (category: CategoryFilter) =>
    request<Task>(category ? `/task?category_code=${encodeURIComponent(category)}` : "/task"),

  // Ometre és definitiu: el sampler exclou la cel·la omesa igual que una de votada.
  skipTask: (token: string) =>
    request<{ status: string }>("/task/skip", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  progress: () => request<Progress>("/task/progress"),

  // Públic: no cal sessió per veure el rànquing.
  ranking: (category: CategoryFilter) =>
    request<Ranking>(
      category ? `/ranking?category_code=${encodeURIComponent(category)}` : "/ranking",
    ),

  vote: (token: string, winner: Winner) =>
    request<{ status: string }>("/vote", {
      method: "POST",
      body: JSON.stringify({ token, winner }),
    }),
};
