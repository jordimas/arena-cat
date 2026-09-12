/** Diferència per paraules entre el text original i la resposta d'un model.
 *
 * És el que fa avaluable la categoria "correcció": sense ressaltar què ha
 * canviat, comparar dos textos gairebé idèntics és molt costós per a l'avaluador.
 *
 * Portat de `html/app.js` (subseqüència comuna més llarga amb programació
 * dinàmica sobre paraules).
 */

export type ChangeType = "same" | "added" | "removed";

export interface Change {
  type: ChangeType;
  text: string;
}

function words(text: string): string[] {
  return text.match(/\S+/g) ?? [];
}

export interface PromptParts {
  /** Què se li demana al model: «Tradueix al valencià…», «Revisa el text…». */
  instruction: string;
  /** El text sobre el qual ha de treballar. Buit si l'enunciat no en porta. */
  source: string;
}

/** Separa l'enunciat en instrucció i text d'origen.
 *
 * Les categories de transformació segueixen la mateixa forma: una instrucció,
 * un salt de línia i el text. Alguns enunciats de correcció acaben la instrucció
 * amb dos punts i no tenen salt, d'aquí el segon intent.
 */
export function splitPrompt(prompt: string): PromptParts {
  const newline = prompt.indexOf("\n");
  if (newline >= 0) {
    return {
      instruction: prompt.slice(0, newline).trim(),
      source: prompt.slice(newline + 1).trim(),
    };
  }

  const colon = prompt.indexOf(":");
  if (colon >= 0) {
    return {
      instruction: prompt.slice(0, colon + 1).trim(),
      source: prompt.slice(colon + 1).trim(),
    };
  }

  return { instruction: prompt.trim(), source: "" };
}

export function diffWords(original: string, revised: string): Change[] {
  const source = words(original);
  const target = words(revised);

  // dp[i][j] = longitud de la subseqüència comuna més llarga de source[i:] i target[j:].
  const dp: number[][] = Array.from({ length: source.length + 1 }, () =>
    new Array<number>(target.length + 1).fill(0),
  );

  for (let i = source.length - 1; i >= 0; i -= 1) {
    for (let j = target.length - 1; j >= 0; j -= 1) {
      dp[i][j] =
        source[i] === target[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  // Recorrem la taula reconstruint el camí òptim.
  const changes: Change[] = [];
  let i = 0;
  let j = 0;
  while (i < source.length || j < target.length) {
    if (i < source.length && j < target.length && source[i] === target[j]) {
      changes.push({ type: "same", text: target[j] });
      i += 1;
      j += 1;
    } else if (j < target.length && (i === source.length || dp[i][j + 1] > dp[i + 1][j])) {
      changes.push({ type: "added", text: target[j] });
      j += 1;
    } else {
      changes.push({ type: "removed", text: source[i] });
      i += 1;
    }
  }
  return changes;
}
