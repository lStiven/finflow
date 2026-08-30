/**
 * Every read and write the screens are allowed to make.
 *
 * Keys are arrays so a mutation can invalidate a whole family at once —
 * declaring an account changes balances *and* adopts waiting movements, so it
 * has to invalidate both lists, not just the one it wrote to.
 */

import {
  queryOptions,
  type UseMutationResult,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { api, unwrap } from "@/api/client";
import type { components, paths } from "@/api/schema";

export type Account = components["schemas"]["AccountResponse"];
export type NetWorth = components["schemas"]["NetWorthResponse"];
export type Transaction = components["schemas"]["TransactionResponse"];
export type Notification = components["schemas"]["NotificationResponse"];
export type RegisteredInbox = components["schemas"]["RegisteredInboxResponse"];
export type Summary = components["schemas"]["SpendingSummaryResponse"];
export type SummaryGroup = components["schemas"]["SummaryGroupResponse"];
export type SpendingTotals = components["schemas"]["SpendingTotalsResponse"];
export type Merchant =
  components["schemas"]["personal_finance__contexts__merchant__presentation__http__router__MerchantResponse"];
export type CategoryOption = components["schemas"]["CategoryResponse"];

export const queryKeys = {
  accounts: ["accounts"] as const,
  transactions: ["transactions"] as const,
  summary: ["summary"] as const,
  merchants: ["merchants"] as const,
  notifications: ["notifications"] as const,
  inbox: ["inbox"] as const,
  catalog: ["catalog"] as const,
};

/* ---------------------------------------------------------------- catalogs */

/**
 * The vocabularies, asked for rather than copied. They come from the same
 * enums the endpoints validate against, so they cannot drift — but only if
 * nothing here hardcodes them. Cached forever: they change on deploy, and a
 * deploy reloads the page anyway.
 */
export const financialCatalogQuery = queryOptions({
  queryKey: [...queryKeys.catalog, "financial"],
  queryFn: () => unwrap(api.GET("/financial/catalog")),
  staleTime: Number.POSITIVE_INFINITY,
});

export const merchantCatalogQuery = queryOptions({
  queryKey: [...queryKeys.catalog, "merchant"],
  queryFn: () => unwrap(api.GET("/merchants/catalog")),
  staleTime: Number.POSITIVE_INFINITY,
});

/* ---------------------------------------------------------------- accounts */

/**
 * The home screen in one call: the accounts and the net worth computed over
 * the same scope. Asking `/financial/net-worth` as well would show two
 * numbers built on different rules — that endpoint always counts closed
 * accounts, this one honours the scope.
 */
export const accountsQuery = (scope: "open" | "closed" | "all" = "open") =>
  queryOptions({
    queryKey: [...queryKeys.accounts, scope],
    queryFn: () =>
      unwrap(api.GET("/financial/accounts", { params: { query: { scope } } })),
  });

export const accountQuery = (accountId: string) =>
  queryOptions({
    queryKey: [...queryKeys.accounts, "detail", accountId],
    queryFn: () =>
      unwrap(
        api.GET("/financial/accounts/{account_id}", {
          params: { path: { account_id: accountId } },
        }),
      ),
  });

/* ------------------------------------------------------------ transactions */

export type TransactionFilters = {
  limit?: number;
  offset?: number;
  unassigned?: boolean;
  account_id?: string;
  merchant_id?: string;
  category?: string;
  origin?: "bank_alert" | "manual";
  direction?: "incoming" | "outgoing";
  search?: string;
  from?: number;
  to?: number;
};

export const transactionsQuery = (filters: TransactionFilters = {}) =>
  queryOptions({
    queryKey: [...queryKeys.transactions, filters],
    queryFn: () =>
      unwrap(
        api.GET("/financial/transactions", {
          // Omitted or a valid value — an enum parameter sent empty is a
          // 422, never "no filter", and `category` is the one here that
          // enforces it. Undefined entries are dropped by the serializer,
          // which is why nothing in `filters` ever defaults to "".
          params: { query: filters },
        }),
      ),
  });

export const transactionQuery = (transactionId: string) =>
  queryOptions({
    queryKey: [...queryKeys.transactions, "detail", transactionId],
    queryFn: () =>
      unwrap(
        api.GET("/financial/transactions/{transaction_id}", {
          params: { path: { transaction_id: transactionId } },
        }),
      ),
  });

export const summaryQuery = (
  groupBy: "month" | "category" | "merchant" | "account",
  filters: Omit<TransactionFilters, "limit" | "offset"> = {},
) =>
  queryOptions({
    queryKey: [...queryKeys.summary, groupBy, filters],
    queryFn: () =>
      unwrap(
        api.GET("/financial/summary", {
          params: { query: { group_by: groupBy, ...filters } },
        }),
      ),
  });

/** Enough merchants to populate a filter, newest activity first. */
export const merchantsForFilterQuery = queryOptions({
  queryKey: [...queryKeys.merchants, "filter"],
  queryFn: () => unwrap(api.GET("/merchants", { params: { query: { limit: 100 } } })),
  staleTime: 5 * 60_000,
});

/* --------------------------------------------------------------- ingestion */

/**
 * What actually arrived. This is the screen that rescues the "nothing is
 * showing up" case, so it polls: the pipeline is asynchronous and there is no
 * push channel to tell us when it moved.
 */
export type NotificationStatus = NonNullable<
  NonNullable<paths["/ingestion/notifications"]["get"]["parameters"]["query"]>["status"]
>;

export const notificationsQuery = (status?: NotificationStatus) =>
  queryOptions({
    queryKey: [...queryKeys.notifications, status ?? "all"],
    queryFn: () =>
      unwrap(
        api.GET("/ingestion/notifications", {
          params: { query: status ? { status } : {} },
        }),
      ),
    refetchInterval: 30_000,
  });

export const inboxQuery = queryOptions({
  queryKey: queryKeys.inbox,
  queryFn: () => unwrap(api.GET("/identity/inbox")),
});

/* --------------------------------------------------------------- mutations */

type InboxBody = components["schemas"]["InboxSendersPayload"];

/**
 * Replaces, never merges — the backend takes the list as final. Callers must
 * send the existing senders plus the new one; there is no partial update, and
 * an empty list means *accept nothing*.
 */
export function useUpdateInbox(): UseMutationResult<RegisteredInbox, Error, InboxBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: InboxBody) => unwrap(api.PATCH("/identity/inbox", { body })),
    onSuccess: (data) => {
      client.setQueryData(queryKeys.inbox, data);
    },
  });
}

type CreateAccountBody = components["schemas"]["OpenAccountPayload"];

/**
 * Declaring an account is retroactive: it adopts the movements that were
 * waiting and recomputes the balance. So the transaction list and the summary
 * are stale the moment this succeeds, not just the account list.
 */
export function useCreateAccount(): UseMutationResult<
  Account,
  Error,
  CreateAccountBody
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateAccountBody) =>
      unwrap(api.POST("/financial/accounts", { body })),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.accounts });
      client.invalidateQueries({ queryKey: queryKeys.transactions });
      client.invalidateQueries({ queryKey: queryKeys.summary });
    },
  });
}

type EnterTransactionBody = components["schemas"]["EnterTransactionPayload"];

/**
 * Money that never emailed, entered by hand.
 *
 * Not retried anywhere — `main.tsx` turns retries off for every mutation
 * because two POSTs are two expenses, and this is the endpoint that rule
 * exists for. Success invalidates the balances as well as the list: a manual
 * movement lands on an account and moves it.
 */
export function useCreateTransaction(): UseMutationResult<
  Transaction,
  Error,
  EnterTransactionBody
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: EnterTransactionBody) =>
      unwrap(api.POST("/financial/transactions", { body })),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.transactions });
      client.invalidateQueries({ queryKey: queryKeys.summary });
      client.invalidateQueries({ queryKey: queryKeys.accounts });
    },
  });
}

type EditTransactionBody = components["schemas"]["EditTransactionPayload"];

/**
 * A correction. Every field is optional and only what is sent changes.
 *
 * `detach` is the one that is not a value but an instruction: it takes the
 * movement off its account, which recomputes that balance. Sending
 * `account_id` and `detach` together is contradictory, so the form offers one
 * or the other and never both.
 */
export function useEditTransaction(
  transactionId: string,
): UseMutationResult<Transaction, Error, EditTransactionBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: EditTransactionBody) =>
      unwrap(
        api.PATCH("/financial/transactions/{transaction_id}", {
          params: { path: { transaction_id: transactionId } },
          body,
        }),
      ),
    onSuccess: (data) => {
      client.setQueryData([...queryKeys.transactions, "detail", transactionId], data);
      client.invalidateQueries({ queryKey: queryKeys.transactions });
      client.invalidateQueries({ queryKey: queryKeys.summary });
      client.invalidateQueries({ queryKey: queryKeys.accounts });
    },
  });
}
