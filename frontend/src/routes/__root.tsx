import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";
import { useAuth } from "@/auth/AuthContext";
import type { Session } from "@/auth/token";
import { AlertsToaster, AlertsWatcher } from "@/components/AlertsCenter";

export type RouterContext = {
  queryClient: QueryClient;
  /** Read in `beforeLoad` guards, so a protected route never renders first. */
  session: Session | null;
};

/**
 * The in-app alerts live here, above every screen, rather than in `AppShell`:
 * each screen draws its own shell, so a toaster there was unmounted on every
 * navigation — a notification disappeared the moment somebody moved — and the
 * poll restarted from scratch. Signed in only: there is nobody to alert on
 * the login screen, and the inbox would answer 401.
 */
function Root() {
  const { session } = useAuth();

  return (
    <>
      <Outlet />
      {session ? (
        <>
          <AlertsWatcher />
          <AlertsToaster />
        </>
      ) : null}
    </>
  );
}

export const Route = createRootRouteWithContext<RouterContext>()({
  // No width here on purpose. A signed-in screen is framed by `AppShell`,
  // which is a sidebar-plus-content layout at desktop widths; a max-width on
  // the shared ancestor would cap that at phone size on every device.
  component: Root,
});
