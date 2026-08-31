import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { setupQuery } from "@/api/queries";
import { useAuth } from "@/auth/AuthContext";
import { type OnboardingAcks, readAcks, writeAck } from "@/onboarding/progress";
import { type OnboardingState, resolveOnboarding } from "@/onboarding/steps";

/**
 * The acknowledgements, held in the query cache rather than in a component.
 *
 * They live in `localStorage`, which is not reactive: written from the guide,
 * the nudge in the shell would keep rendering the old count until something
 * else re-rendered it. Putting them behind a query key means one write
 * updates every reader, which is the same thing the cache already does for
 * the server half of this.
 */
function acksKey(userId: string) {
  return ["onboarding-acks", userId] as const;
}

export type Onboarding = {
  /** Null while the first answer is still in flight, or with no session. */
  state: OnboardingState | null;
  acknowledge: (key: keyof OnboardingAcks) => void;
};

export function useOnboarding(): Onboarding {
  const { session } = useAuth();
  const client = useQueryClient();
  const userId = session?.userId ?? "";

  const { data: setup } = useQuery({ ...setupQuery, enabled: Boolean(session) });
  const { data: acks } = useQuery({
    queryKey: acksKey(userId),
    queryFn: () => readAcks(userId),
    // Reading them is synchronous, so there is no first frame to wait
    // through — without this the guide renders empty for a tick.
    initialData: () => readAcks(userId),
    enabled: Boolean(session),
    staleTime: Number.POSITIVE_INFINITY,
  });

  const acknowledge = useCallback(
    (key: keyof OnboardingAcks) => {
      if (!userId) return;
      client.setQueryData(acksKey(userId), writeAck(userId, key));
    },
    [client, userId],
  );

  return {
    state: setup && acks ? resolveOnboarding(setup, acks) : null,
    acknowledge,
  };
}
