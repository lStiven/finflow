import { useIsMutating, useSuspenseQuery } from "@tanstack/react-query";
import { useState } from "react";
import { inboxQuery, UPDATE_INBOX, useUpdateInbox } from "@/api/queries";
import type { Senders } from "@/onboarding/banks";

/**
 * The approved senders, and the one way this screen writes them.
 *
 * The endpoint replaces the whole list rather than merging, so every write
 * sends both halves, rebuilt from the cached copy. Two writes in flight at
 * once would each rebuild from the same copy and the second would drop what
 * the first added — which is why `busy` counts writes from anywhere in the
 * app, not only this hook's own, and every control that writes is off while
 * it is true.
 *
 * Nothing here is optimistic. A bank is drawn as approved when the server
 * says so, never on the click.
 */
export function useSenders() {
  const { data: inbox } = useSuspenseQuery(inboxQuery);
  const update = useUpdateInbox();
  const busy = useIsMutating({ mutationKey: UPDATE_INBOX }) > 0;
  // Which control asked, so only that one spins.
  const [savingKey, setSavingKey] = useState<string | null>(null);

  const senders: Senders = {
    domains: inbox.allowed_domains,
    addresses: inbox.allowed_addresses,
  };

  function save(next: Senders, key: string, onSaved?: () => void) {
    if (busy) return;
    setSavingKey(key);
    update.mutate(
      { allowed_domains: next.domains, allowed_addresses: next.addresses },
      {
        onSuccess: () => onSaved?.(),
        onSettled: () => setSavingKey(null),
      },
    );
  }

  return {
    senders,
    save,
    busy,
    savingKey,
    error: update.error,
    clearError: update.reset,
  };
}

export type SendersControl = ReturnType<typeof useSenders>;
