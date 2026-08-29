import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";
import type { Session } from "@/auth/token";

export type RouterContext = {
  queryClient: QueryClient;
  /** Read in `beforeLoad` guards, so a protected route never renders first. */
  session: Session | null;
};

export const Route = createRootRouteWithContext<RouterContext>()({
  component: () => (
    <div className="mx-auto min-h-dvh w-full max-w-lg px-5 pt-8 pb-16">
      <Outlet />
    </div>
  ),
});
