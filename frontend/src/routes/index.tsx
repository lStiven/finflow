import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, redirect } from "@tanstack/react-router";
import { Wallet } from "lucide-react";
import { type Account, accountsQuery, type NetWorth } from "@/api/queries";
import { useAuth } from "@/auth/AuthContext";
import { Money } from "@/components/Money";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { describeBalance, signOf } from "@/lib/money";

export const Route = createFileRoute("/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) => context.queryClient.ensureQueryData(accountsQuery("open")),
  component: Dashboard,
});

function Dashboard() {
  const { logout } = useAuth();
  const { data } = useSuspenseQuery(accountsQuery("open"));

  return (
    <main className="flex flex-col gap-8">
      <header className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Finflow</h1>
        <Button variant="quiet" className="px-0 py-0" onClick={logout}>
          Salir
        </Button>
      </header>

      <NetWorthPanel entries={data.net_worth} />
      <AccountList accounts={data.accounts} />
    </main>
  );
}

/**
 * One figure per currency, never a sum. There is no exchange rate anywhere in
 * the backend, so adding COP to USD would be inventing one.
 */
function NetWorthPanel({ entries }: { entries: NetWorth[] }) {
  if (entries.length === 0) {
    return (
      <section>
        <p className="text-sm text-muted">Patrimonio</p>
        <p className="mt-2 text-numeral text-faint tabular">—</p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-6">
      {entries.map((entry) => (
        <div key={entry.currency}>
          <p className="text-sm text-muted">
            Patrimonio
            {entries.length > 1 ? (
              <span className="ml-2 text-faint">{entry.currency}</span>
            ) : null}
          </p>
          <p className="mt-1">
            <Money
              amount={entry.total}
              currency={entry.currency}
              size="lg"
              tone={signOf(entry.total) < 0 ? "negative" : "plain"}
            />
          </p>
          <p className="mt-3 flex gap-5 text-xs text-faint">
            <span>
              Activos{" "}
              <Money
                amount={entry.assets}
                currency={entry.currency}
                size="sm"
                className="text-muted"
              />
            </span>
            <span>
              Deuda{" "}
              <Money
                amount={entry.liabilities}
                currency={entry.currency}
                size="sm"
                className="text-muted"
              />
            </span>
          </p>
        </div>
      ))}
    </section>
  );
}

function AccountList({ accounts }: { accounts: Account[] }) {
  if (accounts.length === 0) {
    return (
      <Card className="flex flex-col items-start gap-3">
        <Wallet className="size-5 text-accent" aria-hidden />
        <div>
          <h2 className="font-medium">Todavía no declaraste ninguna cuenta</h2>
          <p className="mt-1 text-sm text-muted">
            Finflow funciona sin ninguna: todo lo que llegue queda registrado sin
            asignar. Declarar una cuenta es retroactivo — adopta lo que estaba
            esperando.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm text-muted">Cuentas</h2>
      {accounts.map((account) => {
        // Tone and caption both, from the one place allowed to decide what
        // a balance means: a paid-off card must not read "Debes" beside a
        // zero, and deriving the caption from `category` here is exactly how
        // that happens.
        const { tone, label } = describeBalance(
          account.balance,
          account.currency,
          account.category,
        );
        return (
          <Card
            key={account.id}
            className="flex items-center justify-between gap-4 py-4"
          >
            <div className="min-w-0">
              <p className="truncate font-medium">{account.name}</p>
              <p className="mt-0.5 text-xs text-faint">{label}</p>
            </div>
            <Money amount={account.balance} currency={account.currency} tone={tone} />
          </Card>
        );
      })}
    </section>
  );
}
