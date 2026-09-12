/** Tipus compartits, alineats amb els esquemes Pydantic de `backend/app/schemas.py`. */

export type CategoryCode = string;

/** `""` vol dir «qualsevol»: `GET /api/task` sense `category_code` tria la primera pendent. */
export type CategoryFilter = CategoryCode | "";

export interface Category {
  code: CategoryCode;
  name: string;
  description: string | null;
  evaluation_instructions: string | null;
}

/** El backend no revela mai quin model ha generat cada resposta: l'avaluació és cega. */
export interface Task {
  /** Categoria real de la tasca servida, que pot no coincidir amb la triada. */
  category_code: CategoryCode;
  prompt: string;
  response_a: string;
  response_b: string;
  token: string;
}

/** Estat d'autenticació segons `GET /api/auth/session`. */
export interface SessionState {
  authenticated: boolean;
  email: string | null;
  email_verified: boolean;
}

/** Progrés global de l'avaluador, en cel·les (prompt × parella de models). */
export interface Progress {
  total: number;
  voted: number;
  skipped: number;
  remaining: number;
}

export type Winner = "a" | "b" | "tie" | "neither";

export interface RankedModel {
  rank: number;
  model: string;
  bt_skill: number;
}

export interface RankingConfidence {
  category_code: CategoryCode | null;
  best_model: string | null;
  n_prompts: number;
  n_decisive_votes: number;
  p_best_is_best: number;
  confidence_interval: { lo: number; hi: number };
  is_stable: boolean;
}

/** Resposta de `GET /api/ranking`. */
export interface Ranking {
  category_code: CategoryCode | null;
  n_votes_total: number;
  n_votes_decisive: number;
  n_ties: number;
  n_neither: number;
  best_model: string | null;
  ranked_models: RankedModel[];
  confidence: RankingConfidence;
}
