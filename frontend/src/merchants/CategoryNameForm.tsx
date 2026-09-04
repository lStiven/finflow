/**
 * Naming a category, and the one thing the server cannot check for us.
 *
 * Shared by the two places a name is typed — creating one from inside the
 * movement form, and correcting one on the Comercios screen — so the rules a
 * person meets are the same in both: the same length, the same refusal, the
 * same words for it.
 */

import { type SubmitEvent, useState } from "react";
import type { CategoryOption } from "@/api/queries";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { isNameTaken, MAX_CATEGORY_LABEL_LENGTH } from "@/merchants/categories";

/** Below this a name is not a word, and the API refuses it too. */
const MIN_LENGTH = 2;

type Props = {
  label: string;
  /** The name to start from. Empty when this is a new category. */
  initial?: string;
  submitLabel: string;
  pendingLabel: string;
  pending: boolean;
  /** Whatever the last attempt failed with, already in the reader's words. */
  error?: string | null;
  /**
   * Every category already on screen. What a new name is checked against —
   * including this app's Spanish for the shipped ones, which is the half the
   * server has no way to see.
   */
  categories: readonly CategoryOption[];
  onSubmit: (label: string) => void;
  onCancel: () => void;
};

export function CategoryNameForm({
  label,
  initial = "",
  submitLabel,
  pendingLabel,
  pending,
  error,
  categories,
  onSubmit,
  onCancel,
}: Props) {
  const [name, setName] = useState(initial);

  const trimmed = name.trim();
  const taken = isNameTaken(trimmed, categories);
  const canSubmit = !pending && !taken && trimmed.length >= MIN_LENGTH;

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    // This form lives inside the movement form on the Nueva transacción
    // screen. Submitting here must name a category, never save a movement
    // that is only half written — which is what stopping the event does.
    event.stopPropagation();
    if (canSubmit) onSubmit(trimmed);
  }

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-3 rounded-xl border border-violet/25 bg-violet/8 p-3.5"
    >
      <Field
        label={label}
        autoFocus
        maxLength={MAX_CATEGORY_LABEL_LENGTH}
        placeholder="Mascotas"
        hint={`Corta: máximo ${MAX_CATEGORY_LABEL_LENGTH} caracteres, porque se lee en la lista y en las gráficas.`}
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      {taken ? (
        <p role="alert" className="text-outgoing text-sm">
          Ya tienes una categoría que se llama así.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-outgoing text-sm">
          {error}
        </p>
      ) : null}
      <div className="flex gap-3">
        <Button type="button" variant="ghost" full onClick={onCancel}>
          Cancelar
        </Button>
        <Button type="submit" full disabled={!canSubmit}>
          {pending ? pendingLabel : submitLabel}
        </Button>
      </div>
    </form>
  );
}
