/**
 * The one control that both chooses a category and adds one.
 *
 * Adding one is a two-line form, and putting it behind its own screen would
 * mean leaving whatever you were doing — the transaction you were writing, the
 * merchant you were fixing — to go and make a category, then coming back to
 * find the form empty. So the dropdown carries its own last entry, and picking
 * it opens a name field right there.
 *
 * The created category is selected on the way back, which is the whole reason
 * somebody opened this: they are naming *this* movement, and the category was
 * only in the way.
 */

import { useSuspenseQuery } from "@tanstack/react-query";
import { useState } from "react";
import { categoriesQuery, useCreateCategory } from "@/api/queries";
import { Select } from "@/components/ui/Select";
import { CategoryNameForm } from "@/merchants/CategoryNameForm";
import {
  categoryLabel,
  createCategoryError,
  isUncategorized,
} from "@/merchants/categories";

/** The dropdown entry that opens the name field. Never a category value. */
const CREATE = "__create__";

type Props = {
  label?: string;
  /** The selected value, or "" when nothing is chosen yet. */
  value: string;
  onChange: (value: string) => void;
  /** Shown as the first entry, for "leave it out" — omit to force a choice. */
  placeholder?: string;
  hint?: string;
  required?: boolean;
};

export function CategoryPicker({
  label = "Categoría",
  value,
  onChange,
  placeholder,
  hint,
  required,
}: Props) {
  const { data } = useSuspenseQuery(categoriesQuery);
  const create = useCreateCategory();
  const [naming, setNaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onCreate(name: string) {
    setError(null);

    try {
      const created = await create.mutateAsync({ label: name });
      onChange(created.value);
      setNaming(false);
    } catch (cause) {
      setError(createCategoryError(cause));
    }
  }

  if (naming) {
    return (
      <CategoryNameForm
        label="Nueva categoría"
        submitLabel="Crear"
        pendingLabel="Creando…"
        pending={create.isPending}
        error={error}
        categories={data.categories}
        onSubmit={(name) => void onCreate(name)}
        onCancel={() => {
          setNaming(false);
          setError(null);
        }}
      />
    );
  }

  return (
    <Select
      label={label}
      required={required}
      placeholder={placeholder}
      hint={hint}
      value={value}
      onChange={(event) => {
        if (event.target.value === CREATE) {
          setNaming(true);
          return;
        }

        onChange(event.target.value);
      }}
      options={[
        ...data.categories
          // With a placeholder there are two ways to say "Sin categoría" and
          // they are not the same thing: the placeholder sends no category at
          // all, while picking `uncategorized` is a decision, and a decision
          // confirms the merchant — quietly taking it out of the review queue
          // for somebody who only meant to leave the field alone.
          .filter((category) => !(placeholder && isUncategorized(category.value)))
          .map((category) => ({
            value: category.value,
            label: categoryLabel(category.value, category.label),
          })),
        { value: CREATE, label: "＋ Crear una categoría…" },
      ]}
    />
  );
}
