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
import { hasPendingLink, LINK_POLL_MS } from "@/alerts/channels";
import { INBOX_PAGE } from "@/alerts/inbox";
import { api, unwrap } from "@/api/client";
import type { components, paths } from "@/api/schema";
import { DISPLAY_TIMEZONE } from "@/lib/dates";

export type Account = components["schemas"]["AccountResponse"];
export type NetWorth = components["schemas"]["NetWorthResponse"];
export type Transaction = components["schemas"]["TransactionResponse"];
export type Notification = components["schemas"]["NotificationResponse"];
export type TransferLeg = components["schemas"]["TransferResponse"];
/** `include` (the list's default) | `exclude` (every total) | `only`. */
export type TransferView = components["schemas"]["TransferView"];
export type RegisteredInbox = components["schemas"]["RegisteredInboxResponse"];
export type Profile = components["schemas"]["CurrentUserResponse"];
export type InboxSetup = components["schemas"]["InboxSetupResponse"];
export type Summary = components["schemas"]["SpendingSummaryResponse"];
export type SummaryGroup = components["schemas"]["SummaryGroupResponse"];
export type SpendingTotals = components["schemas"]["SpendingTotalsResponse"];
export type SummaryGrouping = components["schemas"]["SummaryGrouping"];
/** `movements` (the default) | `amount`, which needs a pinned `currency`. */
export type SummaryOrder = components["schemas"]["SummaryOrder"];
export type Trend = components["schemas"]["SpendingTrendResponse"];
export type TrendSeries = components["schemas"]["TrendSeriesResponse"];
export type TrendBucket = components["schemas"]["TrendBucketResponse"];
export type TrendInterval = components["schemas"]["TrendInterval"];
export type TrendDimension = components["schemas"]["TrendDimension"];
export type Currency = components["schemas"]["Currency"];
export type Merchant =
  components["schemas"]["personal_finance__contexts__merchant__presentation__http__router__MerchantResponse"];
export type MerchantDetail = components["schemas"]["MerchantDetailResponse"];
/** One spelling a merchant's name arrives under. */
export type MerchantAlias = components["schemas"]["AliasResponse"];
export type MerchantSort = components["schemas"]["MerchantSort"];
/**
 * One category the signed-in person may file a merchant under.
 *
 * `custom` says which half of the vocabulary it came from: false for the ones
 * the app ships, true for the ones they wrote. There is no union type for
 * `value` and there cannot be — half the list is theirs, so it is a plain
 * string and the server is what validates it.
 */
export type CategoryOption = components["schemas"]["CategoryResponse"];
export type InstrumentKind = components["schemas"]["InstrumentKind"];
/** A charge its owner declared, with the two fields the server derives. */
export type Bill = components["schemas"]["BillResponse"];
export type BillsView = components["schemas"]["BillsResponse"];
/** One expected charge. `due_on` is a calendar day, never an instant. */
export type BillOccurrence = components["schemas"]["BillOccurrenceResponse"];
/** Per currency, and two figures: what the month costs and what is still to
 * come. Never summed across currencies. */
export type BillTotal = components["schemas"]["BillTotalResponse"];
export type BillCadence = components["schemas"]["BillCadence"];
export type BillCharge = components["schemas"]["BillChargeResponse"];
export type DeclareBillBody = components["schemas"]["DeclareBillPayload"];
export type AmendBillBody = components["schemas"]["AmendBillPayload"];
/** What a loan costs or an investment earns, and what it will do next. */
export type Financing = components["schemas"]["FinancingResponse"];
export type LoanTerms = components["schemas"]["LoanTermsResponse"];
export type InvestmentTerms = components["schemas"]["InvestmentTermsResponse"];
export type ScheduledPayment = components["schemas"]["ScheduledPaymentResponse"];
export type ProjectedReturn = components["schemas"]["ProjectedReturnResponse"];
export type ChargeAmount = components["schemas"]["ChargeAmountResponse"];
export type Accrual = components["schemas"]["AccrualResponse"];
export type LoanTermsPayload = components["schemas"]["LoanTermsPayload"];
export type InvestmentTermsPayload = components["schemas"]["InvestmentTermsPayload"];
/** `effective_annual` (% E.A.) | `nominal_annual` (N.A. M.V.) | `monthly`. */
export type RateBasis = components["schemas"]["RateBasis"];
export type ChargeBasis = components["schemas"]["ChargeBasis"];
export type AmortizationStyle = components["schemas"]["AmortizationStyle"];
export type AlertChannel = components["schemas"]["ChannelResponse"];
export type AlertPreference = components["schemas"]["PreferenceResponse"];
export type CreatedAlertChannel = components["schemas"]["CreatedChannelResponse"];
export type AlertPreferenceBody = components["schemas"]["PreferencePayload"];

export const queryKeys = {
  accounts: ["accounts"] as const,
  transactions: ["transactions"] as const,
  summary: ["summary"] as const,
  trends: ["trends"] as const,
  merchants: ["merchants"] as const,
  notifications: ["notifications"] as const,
  inbox: ["inbox"] as const,
  profile: ["profile"] as const,
  setup: ["setup"] as const,
  catalog: ["catalog"] as const,
  financing: ["financing"] as const,
  alertChannels: ["alert-channels"] as const,
  // Not `inbox`, which is Identity's forwarding address: this is the alerts
  // the app shows — every movement and Monday's summary.
  alertsInbox: ["alerts-inbox"] as const,
  bills: ["bills"] as const,
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

/**
 * The categories, which are *not* in the catalogue above and cannot be: the
 * shipped ones are the same for everybody, and the rest are whatever this
 * person wrote for themselves.
 *
 * So this one is not cached forever — creating a category invalidates the
 * whole merchant family, and this lives inside it.
 */
export const categoriesQuery = queryOptions({
  queryKey: [...queryKeys.merchants, "categories"],
  queryFn: () => unwrap(api.GET("/merchants/categories")),
  staleTime: 5 * 60_000,
});

/**
 * The same list, plus how many merchants sit in each category.
 *
 * A separate query rather than a flag on the one above, because it is a
 * separate cost: answering it reads the caller's merchants, and the six
 * screens that only need names must not pay for it. Only the panel that
 * manages categories asks — it is what turns "eliminar" into "7 comercios
 * vuelven a Sin categoría".
 */
export const categoryUsageQuery = queryOptions({
  queryKey: [...queryKeys.merchants, "categories", "usage"],
  queryFn: () =>
    unwrap(
      api.GET("/merchants/categories", { params: { query: { with_usage: true } } }),
    ),
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

/**
 * La tabla de amortización, the payoff, and what a position has made.
 *
 * Recomputed on the server every time it is asked and never stored, because
 * it assumes every instalment lands on the day it is due — so this is not
 * cached beyond a request either. Posting a month's interest changes it, and
 * so does any movement on the account, which is why the whole family is
 * invalidated by both.
 */
export const financingQuery = (accountId: string, periods = 12) =>
  queryOptions({
    queryKey: [...queryKeys.financing, accountId, periods],
    queryFn: () =>
      unwrap(
        api.GET("/financial/accounts/{account_id}/financing", {
          params: { path: { account_id: accountId }, query: { periods } },
        }),
      ),
    // An account with no terms answers 409, which is a question nobody has
    // answered yet rather than a failure worth retrying.
    retry: false,
  });

/* ------------------------------------------------------------ transactions */

/**
 * Where a movement came from, straight off the contract.
 *
 * Read from the schema rather than spelled out, because the filter offers
 * whatever `/financial/catalog` serves: written by hand, the list on screen
 * and the list the filter accepts drift the moment an origin is added, and
 * the symptom is a dropdown option that silently does nothing.
 */
export type TransactionOrigin = components["schemas"]["TransactionOrigin"];

export type TransactionFilters = {
  limit?: number;
  offset?: number;
  unassigned?: boolean;
  account_id?: string;
  merchant_id?: string;
  category?: string;
  origin?: TransactionOrigin;
  direction?: "incoming" | "outgoing";
  search?: string;
  from?: number;
  to?: number;
  /**
   * Both sides of a transfer between the owner's own accounts, neither, or
   * only those. Omitted, the API includes them in a list and leaves them out
   * of a summary — so a figure and the list behind it have to agree by asking
   * for the same thing, which is why the dashboard's tiles pass `exclude`.
   */
  transfers?: TransferView;
  /**
   * Pins the page to one currency. Required by `sort: "amount"` — without it
   * the order would be deciding that 100 USD is less than 5.000 COP, which is
   * a fact about the unit and not about the money.
   */
  currency?: Currency;
  /** `date` (newest first, the default) | `amount` (largest first). */
  sort?: "date" | "amount";
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

/**
 * What a breakdown is asked for beyond the filters it shares with the list.
 *
 * The three that only a report uses come with one rule between them:
 * `order: "amount"` and any ranking that follows it need `currency` pinned,
 * because nothing in the backend converts between two of them. Asking without
 * it is a 400, not a guess.
 */
export type SummaryOptions = Omit<TransactionFilters, "limit" | "offset" | "sort"> & {
  order?: SummaryOrder;
  /** Keep this many buckets and add the rest into `others`. */
  top?: number;
  /**
   * Also run the window of equal length immediately before this one, so every
   * bucket carries `previous_totals`. Needs `from` **and** `to`.
   */
  compare?: boolean;
};

export const summaryQuery = (groupBy: SummaryGrouping, filters: SummaryOptions = {}) =>
  queryOptions({
    queryKey: [...queryKeys.summary, groupBy, filters],
    queryFn: () =>
      unwrap(
        api.GET("/financial/summary", {
          params: { query: { group_by: groupBy, ...filters } },
        }),
      ),
  });

export type TrendOptions = Omit<TransactionFilters, "limit" | "offset" | "sort"> & {
  interval?: TrendInterval;
  dimension?: TrendDimension;
  /** How many intervals back from now. Ignored when `from` is given. */
  periods?: number;
  /** Keep this many bands and fold the rest into `others`. */
  series?: number;
  order?: SummaryOrder;
};

/**
 * The stacked chart, in one call.
 *
 * `/financial/summary` answers one dimension at a time, so categories over
 * twelve months would be twelve calls — and each would rank its own buckets
 * independently, so the bands could not be stacked without reconciling them
 * first. This ranks the bands once over the whole range and returns **dense**
 * buckets: every series holds exactly one point per bucket, in the same
 * order, so a chart zips the two by index and never fills a gap.
 */
export const trendQuery = (options: TrendOptions = {}) =>
  queryOptions({
    queryKey: [...queryKeys.trends, options],
    queryFn: () => unwrap(api.GET("/financial/trends", { params: { query: options } })),
  });

/* --------------------------------------------------------------- merchants */

/** Enough merchants to populate a filter, newest activity first. */
export const merchantsForFilterQuery = queryOptions({
  queryKey: [...queryKeys.merchants, "filter"],
  queryFn: () => unwrap(api.GET("/merchants", { params: { query: { limit: 100 } } })),
  staleTime: 5 * 60_000,
});

export type MerchantFilters = {
  search?: string;
  /**
   * A category value, and a plain string on purpose twice over: it reaches
   * this screen from the URL, where anything can be written, and half the
   * vocabulary is whatever the signed-in person wrote for themselves, so
   * there is no union to check it against. The server is what validates it —
   * an unknown value is a 422, which is the right answer to a hand-edited
   * address.
   */
  category?: string;
  /** `true` is the review queue. Omitted means every merchant. */
  needs_review?: boolean;
  sort?: MerchantSort;
  limit?: number;
  offset?: number;
};

export const merchantsQuery = (filters: MerchantFilters = {}) =>
  queryOptions({
    queryKey: [...queryKeys.merchants, "list", filters],
    queryFn: () =>
      unwrap(
        api.GET("/merchants", {
          // Undefined entries are dropped by the serializer, which is what
          // keeps `category` and `sort` from ever being sent empty — an enum
          // parameter sent empty is a 422, never "no filter".
          params: { query: filters },
        }),
      ),
  });

export const merchantQuery = (merchantId: string) =>
  queryOptions({
    queryKey: [...queryKeys.merchants, "detail", merchantId],
    queryFn: () =>
      unwrap(
        api.GET("/merchants/{merchant_id}", {
          params: { path: { merchant_id: merchantId } },
        }),
      ),
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

/**
 * How far this account got connecting their bank.
 *
 * Polled while anything is still open, and only then: two of the four steps
 * close where the user cannot see — Google confirms by mailing a mailbox only
 * this deployment reads, and the first alert lands in a worker — so the
 * screen has to ask. Once expenses are arriving there is nothing left to
 * watch, and `refetchInterval` returning false stops the timer rather than
 * asking forever.
 */
export const setupQuery = queryOptions({
  queryKey: queryKeys.setup,
  queryFn: () => unwrap(api.GET("/ingestion/setup")),
  staleTime: 10_000,
  refetchInterval: (query) => (query.state.data?.ready ? false : 15_000),
});

export const inboxQuery = queryOptions({
  queryKey: queryKeys.inbox,
  queryFn: () => unwrap(api.GET("/identity/inbox")),
});

/**
 * Who is signed in — id, email and name.
 *
 * Read from the API rather than decoded out of the access token: the token
 * carries the same three claims, but as a snapshot of the moment it was
 * issued, so a name changed since then would still show the old one until the
 * next login. Cached long: it changes when this user changes it, and that
 * path writes the answer straight back below.
 */
export const profileQuery = queryOptions({
  queryKey: queryKeys.profile,
  queryFn: () => unwrap(api.GET("/identity/me")),
  staleTime: 10 * 60_000,
});

/* --------------------------------------------------------------- mutations */

type ProfileBody = components["schemas"]["UpdateProfilePayload"];

/**
 * Changes the caller's name, the only editable field for now — the email is
 * the account's identity, and the backend offers no way to move it.
 *
 * The response is the updated profile, so it replaces the cache outright
 * instead of invalidating and asking again.
 */
export function useUpdateProfile(): UseMutationResult<Profile, Error, ProfileBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ProfileBody) => unwrap(api.PATCH("/identity/me", { body })),
    onSuccess: (data) => {
      client.setQueryData(queryKeys.profile, data);
    },
  });
}

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
      // Approving a sender is exactly what closes the `senders_approved`
      // step, so the onboarding answer is stale the moment this lands.
      client.invalidateQueries({ queryKey: queryKeys.setup });
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

type LinkInstrumentBody = components["schemas"]["LinkInstrumentPayload"];

/**
 * Teaches an account another of the names its alerts arrive under.
 *
 * One real account emails as a card for purchases and as an account number
 * for transfers, under different last four digits — link only one and half
 * its movements wait forever. Retroactive like declaring the account itself,
 * so the same three families are stale afterwards.
 */
export function useLinkInstrument(
  accountId: string,
): UseMutationResult<Account, Error, LinkInstrumentBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: LinkInstrumentBody) =>
      unwrap(
        api.POST("/financial/accounts/{account_id}/instruments", {
          params: { path: { account_id: accountId } },
          body,
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.accounts });
      client.invalidateQueries({ queryKey: queryKeys.transactions });
      client.invalidateQueries({ queryKey: queryKeys.summary });
    },
  });
}

/**
 * Stops an account answering to one of the cards it was given.
 *
 * The other half of linking, and the only way a card put on the wrong account
 * ever moves: the movements that arrived under it go back to unassigned, so
 * linking the same card on the right account adopts them there. Both accounts
 * change, which is why this invalidates as widely as linking does.
 */
export function useUnlinkInstrument(
  accountId: string,
): UseMutationResult<Account, Error, LinkInstrumentBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: LinkInstrumentBody) =>
      unwrap(
        api.POST("/financial/accounts/{account_id}/instruments/unlink", {
          params: { path: { account_id: accountId } },
          body,
        }),
      ),
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

type EnterTransferLegBody = components["schemas"]["EnterTransferLegPayload"];

/**
 * A payment between two of your own balances whose other side is not here:
 * a card paid from another bank, from a wallet, or in cash.
 *
 * Unlike `useCreateTransaction` beside it, this one is **idempotent** — the
 * movement id comes from the content, so the same payment sent twice answers
 * with the same movement and moves the balance once. The mutation is still
 * left un-retried like every other, because a retry that succeeded would look
 * like a second payment to a caller reading only the response.
 */
export function useCreateTransferLeg(): UseMutationResult<
  Transaction,
  Error,
  EnterTransferLegBody
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: EnterTransferLegBody) =>
      unwrap(api.POST("/financial/transactions/transfer", { body })),
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

/** Every row an erasure took out, and the balances as they now stand. */
export type ErasedTransactions = components["schemas"]["DeletedTransactionResponse"];

/**
 * Erase a movement and give the balance back what it took.
 *
 * Not `detach`, which only takes it off its account and leaves it counting in
 * what came in and went out. This one leaves nothing: the row is gone and the
 * account holds the money again.
 *
 * The response's `erased` is a **list** because a transfer between two of the
 * owner's own accounts is two rows stating one movement, and the API removes
 * both — so every one of them has to leave the cache, or the other half's
 * detail screen keeps answering for a movement that no longer exists. The
 * trend chart goes too: an erasure is the one write that takes a figure out
 * of a series that is already drawn.
 */
export function useDeleteTransaction(
  transactionId: string,
): UseMutationResult<ErasedTransactions, Error, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.DELETE("/financial/transactions/{transaction_id}", {
          params: { path: { transaction_id: transactionId } },
        }),
      ),
    onSuccess: (data) => {
      for (const erased of data.erased) {
        client.removeQueries({
          queryKey: [...queryKeys.transactions, "detail", erased],
        });
      }
      client.invalidateQueries({ queryKey: queryKeys.transactions });
      client.invalidateQueries({ queryKey: queryKeys.summary });
      client.invalidateQueries({ queryKey: queryKeys.trends });
      client.invalidateQueries({ queryKey: queryKeys.accounts });
      // A schedule is built on the balance the erased row was part of, so a
      // payment taken off a loan leaves its amortization describing a debt
      // that is no longer there.
      client.invalidateQueries({ queryKey: queryKeys.financing });
      // Its alert goes with it: the inbox leaves out alerts about movements
      // that no longer exist, and the bell should not wait for the next poll.
      client.invalidateQueries({ queryKey: queryKeys.alertsInbox });
    },
  });
}

/* ------------------------------------------- transfers declared afterwards */

/**
 * What could be the other side of one movement, before anything changes.
 *
 * Its own query rather than part of the movement's: the API reads every
 * movement the owner has to find one going the other way, so it is asked for
 * only while a detail screen is open, never for a list.
 */
export type TransferOptions = components["schemas"]["TransferOptionsResponse"];
export type TransferAccountOption =
  components["schemas"]["TransferAccountOptionResponse"];
type DeclareTransferBody = components["schemas"]["DeclareTransferPayload"];

export const transferOptionsQuery = (transactionId: string) =>
  queryOptions({
    queryKey: [...queryKeys.transactions, "transfer-options", transactionId],
    queryFn: () =>
      unwrap(
        api.GET("/financial/transactions/{transaction_id}/transfer-options", {
          params: { path: { transaction_id: transactionId } },
        }),
      ),
  });

/**
 * Everything a declaration or its undo can change: which totals count the
 * movement, and — when a side is written or erased — a balance.
 */
function invalidateAfterReclassifying(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: queryKeys.transactions });
  client.invalidateQueries({ queryKey: queryKeys.summary });
  client.invalidateQueries({ queryKey: queryKeys.trends });
  client.invalidateQueries({ queryKey: queryKeys.accounts });
  client.invalidateQueries({ queryKey: queryKeys.financing });
}

/**
 * Say a movement was money between two of the owner's own balances.
 *
 * `counterpart_account_id` writes the other side on that account (a card's
 * debt falls); `counterpart_movement_id` pairs it with a movement already
 * here; neither leaves the other side outside Finflow.
 */
export function useDeclareTransfer(
  transactionId: string,
): UseMutationResult<
  components["schemas"]["TransferDeclaredResponse"],
  Error,
  DeclareTransferBody
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: DeclareTransferBody) =>
      unwrap(
        api.POST("/financial/transactions/{transaction_id}/transfer", {
          params: { path: { transaction_id: transactionId } },
          body,
        }),
      ),
    onSuccess: (data) => {
      for (const movement of data.transactions) {
        client.setQueryData(
          [...queryKeys.transactions, "detail", movement.id],
          movement,
        );
      }
      invalidateAfterReclassifying(client);
    },
  });
}

/**
 * Take a declared transfer back, from either side. A side Finflow wrote is
 * erased, so its detail leaves the cache rather than answering for a row
 * that no longer exists.
 */
export function useUndoTransfer(
  transactionId: string,
): UseMutationResult<components["schemas"]["TransferUndoneResponse"], Error, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.DELETE("/financial/transactions/{transaction_id}/transfer", {
          params: { path: { transaction_id: transactionId } },
        }),
      ),
    onSuccess: (data) => {
      for (const erased of data.erased) {
        client.removeQueries({
          queryKey: [...queryKeys.transactions, "detail", erased],
        });
      }
      for (const movement of data.transactions) {
        client.setQueryData(
          [...queryKeys.transactions, "detail", movement.id],
          movement,
        );
      }
      invalidateAfterReclassifying(client);
    },
  });
}

type RenameAccountBody = components["schemas"]["RenameAccountPayload"];

/**
 * A new name for an account. Nothing else about it moves.
 *
 * The summary is invalidated too: grouped by account, its bucket labels are
 * these names, so a rename that only refreshed the accounts list would leave
 * the old one sitting on a chart.
 */
export function useRenameAccount(
  accountId: string,
): UseMutationResult<Account, Error, RenameAccountBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RenameAccountBody) =>
      unwrap(
        api.PATCH("/financial/accounts/{account_id}", {
          params: { path: { account_id: accountId } },
          body,
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.accounts });
      client.invalidateQueries({ queryKey: queryKeys.summary });
    },
  });
}

type RestateBalanceBody = components["schemas"]["RestateBalancePayload"];

/**
 * What the account holds *today*, as the bank shows it.
 *
 * Not a movement and never recorded as one: the backend solves the opening
 * balance backwards so the same movements still add up to the figure sent, so
 * the transaction list is untouched and only balances and net worth move.
 */
export function useRestateBalance(
  accountId: string,
): UseMutationResult<Account, Error, RestateBalanceBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RestateBalanceBody) =>
      unwrap(
        api.PUT("/financial/accounts/{account_id}/balance", {
          params: { path: { account_id: accountId } },
          body,
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.accounts });
    },
  });
}

type CreditLimitBody = components["schemas"]["SetCreditLimitPayload"];

/** The card's ceiling. `null` clears it — the endpoint takes the whole fact. */
export function useSetCreditLimit(
  accountId: string,
): UseMutationResult<Account, Error, CreditLimitBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreditLimitBody) =>
      unwrap(
        api.PUT("/financial/accounts/{account_id}/credit-limit", {
          params: { path: { account_id: accountId } },
          body,
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.accounts });
    },
  });
}

/**
 * Stop taking movements on an account, keeping everything it already explains.
 *
 * Not a delete — there is no endpoint that deletes one, deliberately: a closed
 * account still accounts for past spending, and its balance still counts in
 * net worth. It leaves the `open` list, which is the list every screen asks
 * for, so the accounts screen has to offer a way back to it.
 */
export function useCloseAccount(
  accountId: string,
): UseMutationResult<Account, Error, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/financial/accounts/{account_id}/close", {
          params: { path: { account_id: accountId } },
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.accounts });
      client.invalidateQueries({ queryKey: queryKeys.summary });
    },
  });
}

/**
 * Takes movements again on an account that was closed by mistake.
 *
 * Nothing is restored — the history never went anywhere. What comes back is
 * the account itself, into the `open` list every screen asks for.
 */
export function useReopenAccount(
  accountId: string,
): UseMutationResult<Account, Error, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/financial/accounts/{account_id}/reopen", {
          params: { path: { account_id: accountId } },
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.accounts });
      client.invalidateQueries({ queryKey: queryKeys.summary });
    },
  });
}

/* --------------------------------------------------- mutations: comercios */

/**
 * Every merchant write ripples further than the merchant list.
 *
 * The canonical merchant travels on each movement (`transaction.merchant`)
 * and it is what labels the buckets of `/financial/summary?group_by=merchant`
 * — so renaming one, moving a spelling or merging two leaves the old name
 * sitting on a list and on a chart unless those go too. Invalidating the
 * whole `merchants` family rather than one key is deliberate: `move` returns
 * the *target* merchant, not the one in the path, and both of them changed.
 */
function useMerchantMutation<TBody>(
  mutationFn: (body: TBody) => Promise<MerchantDetail>,
): UseMutationResult<MerchantDetail, Error, TBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.merchants });
      client.invalidateQueries({ queryKey: queryKeys.transactions });
      client.invalidateQueries({ queryKey: queryKeys.summary });
    },
  });
}

type CategoryNameBody = components["schemas"]["CategoryNamePayload"];

/**
 * A category of one's own, for spending the shipped list does not describe.
 *
 * The name is the whole request: the value its merchants are stored under is
 * derived from it on the server and is not something this side picks. 409
 * when the name is already taken — accents and case folded away, so "Mascotas"
 * and "mascotas" are one category.
 */
export function useCreateCategory(): UseMutationResult<
  CategoryOption,
  Error,
  CategoryNameBody
> {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (body: CategoryNameBody) =>
      unwrap(api.POST("/merchants/categories", { body })),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.merchants });
    },
  });
}

/**
 * Fix a category's name.
 *
 * Only the name changes. The value merchants are filed under is opaque and
 * permanent, so nothing moves and every screen showing that category reads
 * the new word at once — which is why this invalidates the merchant family
 * (the categories live in it) and nothing else.
 */
export function useRenameCategory(): UseMutationResult<
  CategoryOption,
  Error,
  { value: string; label: string }
> {
  const client = useQueryClient();

  return useMutation({
    mutationFn: ({ value, label }: { value: string; label: string }) =>
      unwrap(
        api.PATCH("/merchants/categories/{category_key}", {
          params: { path: { category_key: value } },
          body: { label },
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.merchants });
    },
  });
}

export type DeletedCategory = components["schemas"]["DeletedCategoryResponse"];

/**
 * Remove a category, and hear what moved with it.
 *
 * Everything filed under it goes back to "Sin categoría", and the movements
 * of those merchants follow — so this invalidates the money screens too, not
 * just the merchant list.
 */
export function useDeleteCategory(): UseMutationResult<DeletedCategory, Error, string> {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (value: string) =>
      unwrap(
        api.DELETE("/merchants/categories/{category_key}", {
          params: { path: { category_key: value } },
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.merchants });
      client.invalidateQueries({ queryKey: queryKeys.transactions });
      client.invalidateQueries({ queryKey: queryKeys.summary });
      client.invalidateQueries({ queryKey: queryKeys.trends });
    },
  });
}

type EditMerchantBody = components["schemas"]["EditMerchantPayload"];

/**
 * A new name, a new category, or both — and either one counts as reviewing
 * it, so the merchant leaves the queue as a side effect of being edited.
 *
 * The payload refuses to be empty (422): a caller must send something to
 * change, which is why the form only submits fields that actually differ.
 */
export function useEditMerchant(
  merchantId: string,
): UseMutationResult<MerchantDetail, Error, EditMerchantBody> {
  return useMerchantMutation((body) =>
    unwrap(
      api.PATCH("/merchants/{merchant_id}", {
        params: { path: { merchant_id: merchantId } },
        body,
      }),
    ),
  );
}

/** "It is fine as it is", guessed spellings included. Clears the review flag. */
export function useConfirmMerchant(
  merchantId: string,
): UseMutationResult<MerchantDetail, Error, void> {
  return useMerchantMutation(() =>
    unwrap(
      api.POST("/merchants/{merchant_id}/confirm", {
        params: { path: { merchant_id: merchantId } },
      }),
    ),
  );
}

type MoveAliasBody = components["schemas"]["MoveAliasPayload"];

/**
 * This spelling belongs to a different merchant.
 *
 * Returns the merchant it moved *to*, not the one it came from. Permanent in
 * the sense that matters: from here on that spelling resolves by exact match
 * and no rule re-derives where it belongs — undoing it means moving it back,
 * not waiting for the system to change its mind.
 */
export function useMoveAlias(
  merchantId: string,
): UseMutationResult<MerchantDetail, Error, MoveAliasBody> {
  return useMerchantMutation((body) =>
    unwrap(
      api.POST("/merchants/{merchant_id}/aliases/move", {
        params: { path: { merchant_id: merchantId } },
        body,
      }),
    ),
  );
}

type SplitAliasBody = components["schemas"]["SplitAliasPayload"];

/**
 * This spelling is its own business. Returns the merchant just created.
 *
 * Taking the last spelling out is a 409 — a merchant with none does not
 * exist — so the screen refuses it before the call (`lastAliasBlocker`),
 * which guards the move above for exactly the same reason.
 */
export function useSplitAlias(
  merchantId: string,
): UseMutationResult<MerchantDetail, Error, SplitAliasBody> {
  return useMerchantMutation((body) =>
    unwrap(
      api.POST("/merchants/{merchant_id}/aliases/split", {
        params: { path: { merchant_id: merchantId } },
        body,
      }),
    ),
  );
}

type MergeMerchantsBody = components["schemas"]["MergeMerchantsPayload"];

/**
 * Two records, one business. The merchant in the path survives and takes
 * everything the other had; the absorbed one stops existing.
 *
 * There is no unmerge, which is the whole reason this one asks first.
 */
export function useMergeMerchants(
  merchantId: string,
): UseMutationResult<MerchantDetail, Error, MergeMerchantsBody> {
  return useMerchantMutation((body) =>
    unwrap(
      api.POST("/merchants/{merchant_id}/merge", {
        params: { path: { merchant_id: merchantId } },
        body,
      }),
    ),
  );
}

/* -------------------------------------------------- mutations: financiación */

/**
 * Every financing write moves more than the account it names.
 *
 * Declaring terms changes what the next month will charge; posting a month
 * writes ledger rows and moves a balance. So all of it invalidates the
 * accounts, the movements and the totals — an interest charge is a real
 * expense and belongs in the month's spending, and a screen still showing the
 * old figure would be showing a debt that has since grown.
 */
function invalidateFinancing(client: ReturnType<typeof useQueryClient>): void {
  client.invalidateQueries({ queryKey: queryKeys.accounts });
  client.invalidateQueries({ queryKey: queryKeys.financing });
  client.invalidateQueries({ queryKey: queryKeys.transactions });
  client.invalidateQueries({ queryKey: queryKeys.summary });
}

/**
 * What the loan costs. Loans and mortgages only.
 *
 * PUT, so the body carries the whole fact: sending it again replaces the
 * terms rather than merging into them. Every period already posted stays as
 * it was — a rate corrected today did not change what last March charged.
 */
export function useSetLoanTerms(
  accountId: string,
): UseMutationResult<Account, Error, LoanTermsPayload> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: LoanTermsPayload) =>
      unwrap(
        api.PUT("/financial/accounts/{account_id}/loan", {
          params: { path: { account_id: accountId } },
          body,
        }),
      ),
    onSuccess: () => invalidateFinancing(client),
  });
}

/** How the investment earns. Investment accounts only. */
export function useSetInvestmentTerms(
  accountId: string,
): UseMutationResult<Account, Error, InvestmentTermsPayload> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: InvestmentTermsPayload) =>
      unwrap(
        api.PUT("/financial/accounts/{account_id}/investment", {
          params: { path: { account_id: accountId } },
          body,
        }),
      ),
    onSuccess: () => invalidateFinancing(client),
  });
}

/** Stop computing. Every month already charged stays in the ledger. */
export function useClearFinancing(
  accountId: string,
): UseMutationResult<Account, Error, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.DELETE("/financial/accounts/{account_id}/financing", {
          params: { path: { account_id: accountId } },
        }),
      ),
    onSuccess: () => invalidateFinancing(client),
  });
}

/**
 * Post what the closed months charged, as movements.
 *
 * Safe to press twice, unlike every other mutation here: each charge is
 * identified by its account and its period, so the second attempt lands on a
 * key the ledger already holds and `skipped` says how many. That is why this
 * one *can* be offered as a plain button rather than guarded behind a
 * confirmation.
 *
 * Per account, deliberately, even though `POST /financial/accrue` sweeps them
 * all. A write that happens because a screen loaded is a write nobody asked
 * for; the sweep is the shape a scheduled run wants, not the shape a person
 * does.
 */
export function useAccrue(accountId: string): UseMutationResult<Accrual, Error, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/financial/accounts/{account_id}/accrue", {
          params: { path: { account_id: accountId } },
          // Bogotá, not the browser's zone, and for the same reason every
          // date on screen renders there: a cut on the 15th is the 15th where
          // the account is held, and somebody reading this from another
          // continent must not close a period a day early.
          body: { timezone: DISPLAY_TIMEZONE },
        }),
      ),
    onSuccess: () => invalidateFinancing(client),
  });
}

type RevalueBody = components["schemas"]["RevaluePayload"];

/**
 * What an investment is worth today, with the difference kept as a movement.
 *
 * Deliberately not `useRestateBalance`, which is the right call for a savings
 * account and the wrong one here: a restatement hides the gain inside the
 * opening balance, and a position whose return is invisible reads exactly
 * like a savings account.
 */
export function useRevalue(
  accountId: string,
): UseMutationResult<Accrual, Error, RevalueBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RevalueBody) =>
      unwrap(
        api.POST("/financial/accounts/{account_id}/value", {
          params: { path: { account_id: accountId } },
          body,
        }),
      ),
    onSuccess: () => invalidateFinancing(client),
  });
}

/* ------------------------------------------------------------------ alerts */

/**
 * The Telegram channels this account has, bound or still waiting.
 *
 * Polled while a link is outstanding, and only then. Binding happens inside
 * Telegram, where the page cannot see it, so asking is the only way it finds
 * out — and `refetchInterval` returning false is what stops the timer once
 * there is nothing left to find out. The same shape `/ingestion/setup` uses.
 */
export type AlertsInboxEntry = components["schemas"]["InboxEntryResponse"];
export type AlertsInboxMovement = components["schemas"]["InboxMovementResponse"];
export type AlertsInboxSummary = components["schemas"]["InboxSummaryResponse"];

/** How often a visible tab asks for new alerts. Hidden tabs do not ask. */
export const ALERTS_POLL_MS = 15_000;

/**
 * The alerts the app shows, newest first — what reached Telegram, and the
 * same for somebody with no channel at all.
 *
 * Polled while the page is visible: a deployment without a socket gets as
 * close to real time as a request every few seconds, and a hidden tab asking
 * all afternoon would be reads nobody sees.
 */
export const alertsInboxQuery = queryOptions({
  queryKey: queryKeys.alertsInbox,
  queryFn: () =>
    unwrap(api.GET("/alerts/inbox", { params: { query: { limit: INBOX_PAGE } } })),
  refetchInterval: ALERTS_POLL_MS,
  refetchIntervalInBackground: false,
  refetchOnWindowFocus: true,
  staleTime: 5_000,
});

type InboxPage = { entries: AlertsInboxEntry[] };

/**
 * Hide one alert from the app. Taken off the list at once — the next poll
 * would agree — and put back if the server refuses.
 */
export function useDismissAlert() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (entryId: string) =>
      unwrap(
        api.DELETE("/alerts/inbox/{entry_id}", {
          params: { path: { entry_id: entryId } },
        }),
      ),
    onMutate: async (entryId) => {
      await client.cancelQueries({ queryKey: queryKeys.alertsInbox });
      const before = client.getQueryData<InboxPage>(queryKeys.alertsInbox);
      client.setQueryData<InboxPage>(queryKeys.alertsInbox, (page) =>
        page ? { entries: page.entries.filter((entry) => entry.id !== entryId) } : page,
      );
      return { before };
    },
    onError: (_error, _entryId, context) => {
      if (context?.before) client.setQueryData(queryKeys.alertsInbox, context.before);
    },
    onSettled: () => client.invalidateQueries({ queryKey: queryKeys.alertsInbox }),
  });
}

/** Hide every alert this user has in the app. */
export function useDismissAllAlerts() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/alerts/inbox")),
    onMutate: async () => {
      await client.cancelQueries({ queryKey: queryKeys.alertsInbox });
      const before = client.getQueryData<InboxPage>(queryKeys.alertsInbox);
      client.setQueryData<InboxPage>(queryKeys.alertsInbox, { entries: [] });
      return { before };
    },
    onError: (_error, _vars, context) => {
      if (context?.before) client.setQueryData(queryKeys.alertsInbox, context.before);
    },
    onSettled: () => client.invalidateQueries({ queryKey: queryKeys.alertsInbox }),
  });
}

export const alertChannelsQuery = queryOptions({
  queryKey: queryKeys.alertChannels,
  queryFn: () => unwrap(api.GET("/alerts/channels")),
  staleTime: 10_000,
  refetchInterval: (query) =>
    hasPendingLink(query.state.data?.channels) ? LINK_POLL_MS : false,
});

/**
 * Open a channel and get the one copy of its deep link.
 *
 * The link is in this response and in no other. It is never stored and
 * `GET /alerts/channels` cannot hand it back, so the caller keeps it in
 * component state for as long as the person needs to tap it — never in
 * `localStorage`, which would leave a live credential on the device.
 */
export function useCreateAlertChannel(): UseMutationResult<
  CreatedAlertChannel,
  Error,
  void
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/alerts/channels", { body: { kind: "telegram" } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.alertChannels });
    },
  });
}

export function useUpdateAlertPreference(): UseMutationResult<
  AlertChannel,
  Error,
  { channelId: string; body: AlertPreferenceBody }
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ channelId, body }) =>
      unwrap(
        api.PATCH("/alerts/channels/{channel_id}", {
          params: { path: { channel_id: channelId } },
          body,
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.alertChannels });
    },
  });
}

export function useDeleteAlertChannel(): UseMutationResult<unknown, Error, string> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (channelId: string) =>
      unwrap(
        api.DELETE("/alerts/channels/{channel_id}", {
          params: { path: { channel_id: channelId } },
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.alertChannels });
    },
  });
}

/* ------------------------------------------------------------------ bills */

/**
 * Declared bills and what the window holds.
 *
 * The window is the server's business: left out, it answers for the calendar
 * month in `DISPLAY_TIMEZONE`, which is the only month a screen ever wants.
 * Sending one computed here would let a clock a few hours off ask for the
 * wrong month.
 */
export const billsQuery = queryOptions({
  queryKey: queryKeys.bills,
  queryFn: () =>
    unwrap(
      api.GET("/financial/bills", {
        params: { query: { timezone: DISPLAY_TIMEZONE } },
      }),
    ),
});

export function useDeclareBill(): UseMutationResult<Bill, Error, DeclareBillBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: DeclareBillBody) =>
      unwrap(api.POST("/financial/bills", { body })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.bills });
    },
  });
}

export function useAmendBill(
  billId: string,
): UseMutationResult<Bill, Error, AmendBillBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: AmendBillBody) =>
      unwrap(
        api.PATCH("/financial/bills/{bill_id}", {
          params: { path: { bill_id: billId } },
          body,
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.bills });
    },
  });
}

/**
 * Pause or resume, as one mutation.
 *
 * Two hooks would be two places to forget the invalidation, and the caller
 * already knows which way it is going from the bill it is looking at.
 */
export function usePauseBill(): UseMutationResult<
  Bill,
  Error,
  { billId: string; paused: boolean }
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ billId, paused }: { billId: string; paused: boolean }) =>
      unwrap(
        paused
          ? api.POST("/financial/bills/{bill_id}/pause", {
              params: { path: { bill_id: billId } },
            })
          : api.POST("/financial/bills/{bill_id}/resume", {
              params: { path: { bill_id: billId } },
            }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.bills });
    },
  });
}

export function useForgetBill(): UseMutationResult<unknown, Error, string> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (billId: string) =>
      unwrap(
        api.DELETE("/financial/bills/{bill_id}", {
          params: { path: { bill_id: billId } },
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.bills });
    },
  });
}

/**
 * Answering for one charge: it happened, it did not, or undo either.
 *
 * One hook for all four, like `usePauseBill`, and here it matters more: this
 * is the only mutation on this screen that moves money, so **everything it
 * touches has to be invalidated together**. A confirmed charge writes a ledger
 * row and changes a balance, so the movements list, the accounts, net worth
 * and the summary are all stale the instant it returns — and a screen showing
 * a balance that has not caught up is the kind of wrong number somebody stops
 * trusting the app over.
 *
 * `period` is the charge's due date, which is its identity. Sending it in the
 * path rather than the body is what makes paying September and paying October
 * two different requests.
 */
export type ChargeAction = "pay" | "unpay" | "skip" | "unskip";

export type SettleChargeVariables = {
  billId: string;
  period: string;
  action: ChargeAction;
  /** What actually moved, when it is not what the bill says. */
  amount?: string;
  /**
   * The currency that amount is in — the charge's own, never a constant.
   *
   * The server refuses an amount in a currency the bill is not in rather than
   * converting it, so hardcoding one here is a USD bill that can never be
   * confirmed from the screen.
   */
  currency?: Currency;
  /** When it actually moved, in epoch seconds. */
  occurredAt?: number;
};

export function useSettleCharge(): UseMutationResult<
  BillCharge,
  Error,
  SettleChargeVariables
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ billId, period, action, amount, currency, occurredAt }) => {
      const params = {
        path: { bill_id: billId, period },
      } as const;

      if (action === "pay") {
        return unwrap(
          api.POST("/financial/bills/{bill_id}/occurrences/{period}/pay", {
            params,
            body: {
              ...(amount === undefined ? {} : { amount, currency }),
              ...(occurredAt === undefined ? {} : { occurred_at: occurredAt }),
            },
          }),
        );
      }

      if (action === "unpay") {
        return unwrap(
          api.DELETE("/financial/bills/{bill_id}/occurrences/{period}/pay", {
            params,
          }),
        );
      }

      if (action === "skip") {
        return unwrap(
          api.POST("/financial/bills/{bill_id}/occurrences/{period}/skip", { params }),
        );
      }

      return unwrap(
        api.DELETE("/financial/bills/{bill_id}/occurrences/{period}/skip", { params }),
      );
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.bills });
      void client.invalidateQueries({ queryKey: queryKeys.transactions });
      void client.invalidateQueries({ queryKey: queryKeys.summary });
      void client.invalidateQueries({ queryKey: queryKeys.trends });
      void client.invalidateQueries({ queryKey: queryKeys.accounts });
    },
  });
}
