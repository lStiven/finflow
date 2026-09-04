/**
 * Managing the categories somebody wrote, on the screen they belong to.
 *
 * Not a screen of its own: a category only means anything as the bucket a
 * comercio sits in, and this is the screen about comercios. It sits collapsed
 * under the filters so the merchant list stays the subject, and opens to the
 * three things a person actually wants — add one, fix a name they mistyped,
 * get rid of one they do not use.
 *
 * The categories the app ships are not in here. They are the same for
 * everybody, nobody may edit them, and listing sixteen unactionable rows above
 * the two that are actionable would bury the point of the panel.
 */

import { useSuspenseQuery } from "@tanstack/react-query";
import { ChevronDown, Pencil, Tag, Trash2, TriangleAlert } from "lucide-react";
import { useState } from "react";
import {
  type CategoryOption,
  categoryUsageQuery,
  useCreateCategory,
  useDeleteCategory,
  useRenameCategory,
} from "@/api/queries";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import { CategoryNameForm } from "@/merchants/CategoryNameForm";
import { createCategoryError } from "@/merchants/categories";

/** Which row, if any, has taken the panel over. */
type Editing =
  | { kind: "none" }
  | { kind: "create" }
  | { kind: "rename"; value: string }
  | { kind: "delete"; value: string };

export function CategoryManager() {
  const { data } = useSuspenseQuery(categoryUsageQuery);
  const [open, setOpen] = useState(false);

  const mine = data.categories.filter((category) => category.custom);

  return (
    <Card lift={false} className="p-0">
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3.5 text-left"
      >
        <Tag className="size-4 shrink-0 text-cyan" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="block text-sm text-text">Tus categorías</span>
          <span className="mt-0.5 block text-faint text-xs">
            {mine.length === 0
              ? "Ninguna todavía. Las que trae Finflow siempre están."
              : `${mine.length} tuya${mine.length === 1 ? "" : "s"}, además de las que trae Finflow.`}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-faint transition-transform duration-200",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </button>

      {open ? <Panel categories={data.categories} mine={mine} /> : null}
    </Card>
  );
}

function Panel({
  categories,
  mine,
}: {
  /** The whole vocabulary — what a new name is checked against. */
  categories: readonly CategoryOption[];
  mine: CategoryOption[];
}) {
  const create = useCreateCategory();
  const [editing, setEditing] = useState<Editing>({ kind: "none" });
  const [error, setError] = useState<string | null>(null);

  async function onCreate(label: string) {
    setError(null);

    try {
      await create.mutateAsync({ label });
      setEditing({ kind: "none" });
    } catch (cause) {
      setError(createCategoryError(cause));
    }
  }

  return (
    <div className="flex flex-col gap-3 border-line border-t p-4">
      <p className="text-muted text-xs leading-relaxed">
        Cambiarle el nombre a una categoría no mueve nada: los comercios que estén en
        ella se quedan donde están y el nombre nuevo se ve al instante, también en los
        movimientos de antes. Borrarla sí los devuelve a «Sin categoría».
      </p>

      {mine.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {mine.map((category) => (
            <li key={category.value}>
              <Row
                category={category}
                categories={categories}
                editing={editing}
                onEditing={setEditing}
              />
            </li>
          ))}
        </ul>
      ) : null}

      {editing.kind === "create" ? (
        <CategoryNameForm
          label="Nueva categoría"
          submitLabel="Crear"
          pendingLabel="Creando…"
          pending={create.isPending}
          error={error}
          categories={categories}
          onSubmit={(label) => void onCreate(label)}
          onCancel={() => {
            setEditing({ kind: "none" });
            setError(null);
          }}
        />
      ) : (
        <Button
          variant="ghost"
          className="self-start py-2 text-xs"
          onClick={() => setEditing({ kind: "create" })}
        >
          ＋ Nueva categoría
        </Button>
      )}
    </div>
  );
}

function Row({
  category,
  categories,
  editing,
  onEditing,
}: {
  category: CategoryOption;
  categories: readonly CategoryOption[];
  editing: Editing;
  onEditing: (editing: Editing) => void;
}) {
  const rename = useRenameCategory();
  const remove = useDeleteCategory();
  const [error, setError] = useState<string | null>(null);

  const mine =
    editing.kind !== "none" && "value" in editing && editing.value === category.value;

  async function onRename(label: string) {
    setError(null);

    try {
      await rename.mutateAsync({ value: category.value, label });
      onEditing({ kind: "none" });
    } catch (cause) {
      setError(createCategoryError(cause));
    }
  }

  async function onDelete() {
    setError(null);

    try {
      await remove.mutateAsync(category.value);
      onEditing({ kind: "none" });
    } catch {
      setError("No se pudo borrar la categoría.");
    }
  }

  if (mine && editing.kind === "rename") {
    return (
      <CategoryNameForm
        label="Nombre"
        initial={category.label}
        submitLabel="Guardar"
        pendingLabel="Guardando…"
        pending={rename.isPending}
        error={error}
        // Its own name is not "taken" by anybody else, so correcting only the
        // spelling — mascotas to Mascotas — must not be refused by itself.
        categories={categories.filter((other) => other.value !== category.value)}
        onSubmit={(label) => void onRename(label)}
        onCancel={() => {
          onEditing({ kind: "none" });
          setError(null);
        }}
      />
    );
  }

  if (mine && editing.kind === "delete") {
    return (
      // Shaped like every other confirmation in the app: the same warning
      // colour, the same quiet way out, and the destructive action in ghost
      // rather than in the colour everything else uses for "yes, go".
      <div className="flex flex-col gap-3 rounded-xl border border-outgoing/30 bg-outgoing/[0.06] p-3.5">
        <h3 className="flex items-center gap-2 font-medium text-sm">
          <TriangleAlert className="size-4 shrink-0 text-outgoing" aria-hidden />
          Eliminar «{category.label}»
        </h3>
        <p className="text-muted text-xs leading-relaxed">
          {category.usage
            ? `${category.usage} comercio${category.usage === 1 ? "" : "s"} vuelve${
                category.usage === 1 ? "" : "n"
              } a «Sin categoría», y sus movimientos con ${
                category.usage === 1 ? "él" : "ellos"
              }. No se borra ningún movimiento, y puedes volver a ponerles la categoría que quieras.`
            : "No hay ningún comercio en ella, así que no se mueve nada."}
        </p>
        {error ? (
          <p role="alert" className="text-outgoing text-sm">
            {error}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="ghost"
            className="py-2 text-xs"
            disabled={remove.isPending}
            onClick={() => void onDelete()}
          >
            <Trash2 className="size-3.5" />
            {remove.isPending ? "Eliminando…" : "Eliminar la categoría"}
          </Button>
          <Button
            variant="quiet"
            className="py-2 text-xs"
            disabled={remove.isPending}
            onClick={() => {
              onEditing({ kind: "none" });
              setError(null);
            }}
          >
            Mejor no
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 rounded-xl border border-line px-3 py-2.5">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-text">{category.label}</span>
        <span className="mt-0.5 block text-faint text-xs">
          {category.usage
            ? `${category.usage} comercio${category.usage === 1 ? "" : "s"}`
            : "Sin comercios todavía"}
        </span>
      </span>
      <Button
        variant="quiet"
        aria-label={`Cambiar el nombre de ${category.label}`}
        className="px-2 py-2"
        onClick={() => onEditing({ kind: "rename", value: category.value })}
      >
        <Pencil className="size-3.5" aria-hidden />
      </Button>
      <Button
        variant="quiet"
        aria-label={`Borrar ${category.label}`}
        className="px-2 py-2 hover:text-outgoing"
        onClick={() => onEditing({ kind: "delete", value: category.value })}
      >
        <Trash2 className="size-3.5" aria-hidden />
      </Button>
    </div>
  );
}
