import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";
import type { Session } from "@/auth/token";

export type RouterContext = {
  queryClient: QueryClient;
  /** Read in `beforeLoad` guards, so a protected route never renders first. */
  session: Session | null;
};

export const Route = createRootRouteWithContext<RouterContext>()({
  // No width here on purpose. A signed-in screen is framed by `AppShell`,
  // which is a sidebar-plus-content layout at desktop widths; a max-width on
  // the shared ancestor would cap that at phone size on every device.
  component: () => <Outlet />,
});
