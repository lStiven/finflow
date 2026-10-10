import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { MailWarning, Plug, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { inboxQuery, recentMailQuery, setupQuery } from "@/api/queries";
import { buttonClass } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { useNow } from "@/lib/useNow";
import { healthOf } from "@/onboarding/activity";
import { diagnoseEmpty } from "@/onboarding/diagnosis";

/**
 * An empty list that says why it is empty, with the way to fix it.
 *
 * Plain queries rather than suspense: this is an enrichment of an empty
 * state, and a screen with nothing on it must not wait on — or fail with —
 * an explanation. Until the reason is known, `fallback` is what shows.
 */
export function EmptyDiagnosis({ fallback }: { fallback: ReactNode }) {
  const now = useNow(60_000);
  const { data: setup } = useQuery(setupQuery);
  const { data: mail } = useQuery(recentMailQuery);
  const { data: inbox } = useQuery(inboxQuery);

  const health =
    mail && inbox
      ? healthOf(
          mail.notifications,
          { domains: inbox.allowed_domains, addresses: inbox.allowed_addresses },
          now,
        )
      : null;
  const said = diagnoseEmpty(setup, health);

  if (!said) return <>{fallback}</>;

  return (
    <Notice
      tone={said.tone}
      icon={said.tone === "warn" ? MailWarning : Plug}
      title={said.title}
      className="rise"
    >
      <p>{said.body}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Link
          to="/conectar"
          search={said.action.paso ? { paso: said.action.paso } : {}}
          className={buttonClass("ghost")}
        >
          {said.action.label}
        </Link>
        <Link to="/transacciones/nueva" className={buttonClass("quiet")}>
          <Plus className="size-4" aria-hidden />
          Agregar a mano
        </Link>
      </div>
    </Notice>
  );
}
