import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  use,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api, unwrap } from "@/api/client";
import { clearSession, loadSession, type Session, saveSession } from "@/auth/token";

type AuthValue = {
  session: Session | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const queryClient = useQueryClient();

  const logout = useCallback(() => {
    clearSession();
    setSession(null);
    // Someone else's data must not survive in the cache behind the login form.
    queryClient.clear();
  }, [queryClient]);

  // The client dispatches this when any request comes back 401. Handling it
  // centrally means a token that expired between screens lands on the login
  // form rather than on an error toast per in-flight query.
  useEffect(() => {
    const onUnauthorized = () => {
      setSession(null);
      queryClient.clear();
    };
    window.addEventListener("finflow:unauthorized", onUnauthorized);
    return () => window.removeEventListener("finflow:unauthorized", onUnauthorized);
  }, [queryClient]);

  const adopt = useCallback(
    (token: { user_id: string; access_token: string; expires_at: number }) => {
      const next: Session = {
        userId: token.user_id,
        accessToken: token.access_token,
        expiresAt: token.expires_at,
      };
      saveSession(next);
      setSession(next);
    },
    [],
  );

  const value = useMemo<AuthValue>(
    () => ({
      session,
      login: async (email, password) => {
        adopt(await unwrap(api.POST("/identity/login", { body: { email, password } })));
      },
      register: async (email, password) => {
        // The senders stay empty here on purpose: approving them is its own
        // screen, and an empty allow-list means "accept nothing" — which is
        // the right state for an account that has not connected a bank yet.
        adopt(
          await unwrap(
            api.POST("/identity/register", {
              body: {
                email,
                password,
                allowed_domains: [],
                allowed_addresses: [],
              },
            }),
          ),
        );
      },
      logout,
    }),
    [session, adopt, logout],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthValue {
  const value = use(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
