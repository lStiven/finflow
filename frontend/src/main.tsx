import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode, Suspense, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { AuthProvider, useAuth } from "@/auth/AuthContext";
import { routeTree } from "@/routeTree.gen";
import "@/index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The pipeline is asynchronous and there is no push channel, so coming
      // back to the tab is the closest thing to "something may have changed".
      refetchOnWindowFocus: true,
      staleTime: 30_000,
      retry: (failureCount, error) =>
        // A 401 is answered by the login form, not by trying again; a 404 is
        // this user not owning that thing, which retrying cannot fix.
        !isTerminal(error) && failureCount < 2,
    },
    mutations: {
      // A manual movement is not idempotent — two POSTs are two expenses.
      retry: false,
    },
  },
});

function isTerminal(error: unknown): boolean {
  // Every failure reaches here as an `ApiError`, which carries the status.
  const status = (error as { status?: number } | null)?.status;

  // A 429 is not terminal in the sense the other three are — it is the one
  // failure that *will* pass on its own. It stops the retries anyway, and
  // that is the point: trying again immediately is the one response
  // guaranteed not to help, and on the doors that count every attempt it is
  // the client spending what is left of its own budget.
  return status === 401 || status === 403 || status === 404 || status === 429;
}

const router = createRouter({
  routeTree,
  context: { queryClient, session: null },
  defaultPreload: "intent",
  scrollRestoration: true,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

/**
 * The router's guards read the session from its context, and the session
 * lives in React state — so the provider has to sit above the router and hand
 * it down on every change.
 *
 * Handing it down is not enough on its own: the router merges a new context
 * without re-running `beforeLoad`, so the guards would keep believing
 * whatever they were told when the match was created. The explicit
 * invalidation below is what makes signing in leave `/login` and signing out
 * leave the dashboard — and it is the only thing that answers a 401 arriving
 * from a screen nobody navigated away from, which has no other handler.
 *
 * It runs after commit, so the context the guards re-read is the new one.
 */
function RoutedApp() {
  const { session } = useAuth();

  // `session` is the trigger, not something the body reads. The rule's
  // suggested fix — dropping it — would run this once on mount and never
  // again, which is the exact bug the effect exists to prevent.
  // biome-ignore lint/correctness/useExhaustiveDependencies: explained above
  useEffect(() => {
    void router.invalidate();
  }, [session]);

  return <RouterProvider router={router} context={{ queryClient, session }} />;
}

const container = document.getElementById("root");
if (!container) throw new Error("index.html is missing #root");

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Suspense fallback={null}>
          <RoutedApp />
        </Suspense>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
