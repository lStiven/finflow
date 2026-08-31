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
import {
  clearSession,
  millisecondsUntilExpiry,
  readStoredSession,
  type Session,
  saveSession,
} from "@/auth/token";

/**
 * Why the session ended, when it ended by itself.
 *
 * `null` covers both "there never was one" and "they signed out" — neither is
 * news. Only an expiry is worth interrupting somebody with.
 */
export type SessionEnd = "expired" | null;

type AuthValue = {
  session: Session | null;
  /** Set when the session ran out; the login screen is what reports it. */
  sessionEnd: SessionEnd;
  dismissSessionEnd: () => void;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string) => Promise<void>;
  logout: () => void;
};

type AuthState = {
  session: Session | null;
  sessionEnd: SessionEnd;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [{ session, sessionEnd }, setState] = useState<AuthState>(() => {
    // A tab opened after the token ran out lands here, not on the 401 path:
    // nothing has been requested yet, and the notice has to survive that.
    const stored = readStoredSession();
    return { session: stored.session, sessionEnd: stored.expired ? "expired" : null };
  });
  const queryClient = useQueryClient();

  const logout = useCallback(() => {
    clearSession();
    setState({ session: null, sessionEnd: null });
    // Someone else's data must not survive in the cache behind the login form.
    queryClient.clear();
  }, [queryClient]);

  const expire = useCallback(() => {
    clearSession();
    setState({ session: null, sessionEnd: "expired" });
    queryClient.clear();
  }, [queryClient]);

  // The client dispatches this when any request comes back 401. Handling it
  // centrally means a token that expired between screens lands on the login
  // form rather than on an error toast per in-flight query.
  useEffect(() => {
    window.addEventListener("finflow:unauthorized", expire);
    return () => window.removeEventListener("finflow:unauthorized", expire);
  }, [expire]);

  /*
   * Expiry is announced on a timer rather than discovered on the next
   * request. A tab left open overnight would otherwise sit there looking
   * signed in — every figure on it stale, and nothing said — until somebody
   * clicked. `expiresAt` is known the moment the token arrives, so the moment
   * it lapses is knowable too.
   */
  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(expire, millisecondsUntilExpiry(session));
    return () => window.clearTimeout(timer);
  }, [session, expire]);

  const adopt = useCallback(
    (token: { user_id: string; access_token: string; expires_at: number }) => {
      const next: Session = {
        userId: token.user_id,
        accessToken: token.access_token,
        expiresAt: token.expires_at,
      };
      saveSession(next);
      setState({ session: next, sessionEnd: null });
    },
    [],
  );

  const dismissSessionEnd = useCallback(() => {
    setState((current) => ({ ...current, sessionEnd: null }));
  }, []);

  const value = useMemo<AuthValue>(
    () => ({
      session,
      sessionEnd,
      dismissSessionEnd,
      login: async (email, password) => {
        adopt(await unwrap(api.POST("/identity/login", { body: { email, password } })));
      },
      register: async (email, password, name) => {
        // The senders stay empty here on purpose: approving them is its own
        // screen, and an empty allow-list means "accept nothing" — which is
        // the right state for an account that has not connected a bank yet.
        //
        // The name is optional all the way down: the account is identified by
        // its email, so an empty box registers a nameless account rather than
        // blocking the form.
        adopt(
          await unwrap(
            api.POST("/identity/register", {
              body: {
                email,
                password,
                name: name?.trim() ? name.trim() : null,
                allowed_domains: [],
                allowed_addresses: [],
              },
            }),
          ),
        );
      },
      logout,
    }),
    [session, sessionEnd, dismissSessionEnd, adopt, logout],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthValue {
  const value = use(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
