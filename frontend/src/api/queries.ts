// What each screen reads, how often it refreshes, and what it can change.
// The server is the only source of truth: after every change the affected lists are
// fetched again instead of being patched by hand.
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { ApiError, OfflineError, api, apiPath, getAll, onThisSite } from "./client";
import type {
  Account,
  AccountInput,
  ActivityEvent,
  BotStatus,
  Channel,
  ChannelInput,
  CheckJob,
  CursorPage,
  Health,
  LoginTest,
  PollResult,
  ProfileLookup,
  Settings,
  SettingsInput,
} from "./types";

export const keys = {
  status: ["status"] as const,
  health: ["health"] as const,
  settings: ["settings"] as const,
  accounts: ["accounts"] as const,
  channels: ["channels"] as const,
  activity: (group: string) => ["activity", group] as const,
  recent: ["activity", "recent"] as const,
  latestJob: ["check-jobs", "latest"] as const,
  lookup: (handle: string) => ["lookup", handle] as const,
};

export function retryPolicy(failureCount: number, error: unknown) {
  // Offline: the status poll keeps trying on its own schedule; don't stack retries.
  if (error instanceof OfflineError) return false;
  if (error instanceof ApiError && error.status < 500) return false;
  return failureCount < 2;
}

/** After any change, everything the change can affect is read again. */
function refreshAll(client: QueryClient) {
  return client.invalidateQueries({
    predicate: (query) => query.queryKey[0] !== "lookup" && query.queryKey[0] !== "health",
  });
}

// -- reading ---------------------------------------------------------------------------------------
export function useStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: () => api.get<BotStatus>("/api/status/"),
    // Quick while something is happening, calm otherwise. Keeps polling while offline,
    // so the page reconnects by itself once the bot is back.
    refetchInterval: (query) => {
      if (query.state.error) return 5_000;
      return query.state.data?.scheduler.checking_now ? 3_000 : 10_000;
    },
    retry: retryPolicy,
  });
}

export function useHealth() {
  return useQuery({ queryKey: keys.health, queryFn: () => api.get<Health>("/api/health/"), staleTime: 60_000 });
}

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => api.get<Settings>("/api/settings/") });
}

export function useAccounts() {
  return useQuery({
    queryKey: keys.accounts,
    queryFn: () => getAll<Account>("/api/accounts/"),
    refetchInterval: (query) => {
      const busy = query.state.data?.some((a) => a.checking_now || (a.is_active && a.state === "waiting_first_check"));
      return busy ? 3_000 : 15_000;
    },
  });
}

export function useChannels() {
  return useQuery({ queryKey: keys.channels, queryFn: () => getAll<Channel>("/api/webhooks/") });
}

/** The latest events for the dashboard, without the checker's routine start and stop notes. */
export function useRecentActivity(limit = 6) {
  return useQuery({
    queryKey: keys.recent,
    queryFn: () => api.get<CursorPage<ActivityEvent>>(`/api/activity/?page_size=${limit + 6}`),
    select: (page) => page.results.filter((e) => e.type !== "checker_started" && e.type !== "checker_stopped").slice(0, limit),
    refetchInterval: 15_000,
  });
}

export type ActivityGroup = "all" | "sent" | "problems";

export function useActivity(group: ActivityGroup) {
  return useInfiniteQuery({
    queryKey: keys.activity(group),
    queryFn: ({ pageParam }) => api.get<CursorPage<ActivityEvent>>(pageParam),
    initialPageParam: `/api/activity/?group=${group}&page_size=50`,
    getNextPageParam: (last) => (last.next ? apiPath(last.next) : undefined),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });
}

/** The newest Check All job, or null when there has never been one. */
export function useLatestJob() {
  return useQuery({
    queryKey: keys.latestJob,
    // The list (newest first) rather than /latest/, which answers 404 before the first Check All.
    queryFn: async () => (await api.get<{ results: CheckJob[] }>("/api/check-jobs/?limit=1")).results[0] ?? null,
    refetchInterval: (query) => {
      const job = query.state.data;
      return job && !job.final ? Math.max(0.5, job.poll_after_seconds) * 1000 : false;
    },
  });
}

export function useProfileLookup(handle: string) {
  return useQuery({
    queryKey: keys.lookup(handle),
    queryFn: ({ signal }) =>
      fetch(onThisSite(`/api/bluesky/profile/?handle=${encodeURIComponent(handle)}`), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal,
      }).then(async (res) => {
        // Lookups answer with the same body for every outcome (found, not found, rate limited…).
        const body = (await res.json().catch(() => null)) as ProfileLookup | null;
        if (!body || typeof body.result !== "string") throw new ApiError(res.status, body as never);
        return body;
      }, () => {
        throw new OfflineError();
      }),
    enabled: handle.length > 0,
    staleTime: 60_000,
    retry: false,
  });
}

// -- changing --------------------------------------------------------------------------------------
function useChange<Vars, Result>(fn: (vars: Vars) => Promise<Result>) {
  const client = useQueryClient();
  // Not awaited: the caller's own onSuccess runs first, before the refresh redraws the page.
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      void refreshAll(client);
    },
  });
}

export const useSaveSettings = () => useChange((input: SettingsInput) => api.patch<{ status: string; settings: Settings; warning?: string }>("/api/settings/", input));

/** Never saves anything; blank values mean "the saved one". */
export const useTestLogin = () =>
  useMutation({
    mutationFn: async (input: { bluesky_handle?: string; bluesky_password?: string }): Promise<LoginTest> => {
      try {
        return await api.post<LoginTest>("/api/settings/test-login/", input);
      } catch (error) {
        // 400/429/502/503 answers still carry the full explanation.
        if (error instanceof ApiError && typeof error.body.result === "string") return error.body as unknown as LoginTest;
        throw error;
      }
    },
  });

export const useAddAccount = () => useChange((input: AccountInput) => api.post<Account>("/api/accounts/", input));
export const useUpdateAccount = () =>
  useChange(({ id, ...input }: AccountInput & { id: number }) => api.patch<Account>(`/api/accounts/${id}/`, input));
export const useRemoveAccount = () => useChange((id: number) => api.delete(`/api/accounts/${id}/`));
export const useCheckNow = () => useChange((id: number) => api.post<PollResult>(`/api/accounts/${id}/poll_now/`));

export function useCheckAll() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<CheckJob>("/api/accounts/check_all/"),
    onSuccess: (job) => client.setQueryData(keys.latestJob, job),
  });
}

export const useAddChannel = () => useChange((input: ChannelInput) => api.post<Channel>("/api/webhooks/", input));
export const useUpdateChannel = () =>
  useChange(({ id, ...input }: ChannelInput & { id: number }) => api.patch<Channel>(`/api/webhooks/${id}/`, input));
export const useMakeDefault = () => useChange((id: number) => api.post<Channel>(`/api/webhooks/${id}/make_default/`));
export const useTestChannel = () => useChange((id: number) => api.post<{ status: string }>(`/api/webhooks/${id}/test/`));
export const useRemoveChannel = () =>
  useChange(({ id, reassignTo }: { id: number; reassignTo?: number | "default" }) =>
    api.delete(`/api/webhooks/${id}/${reassignTo !== undefined ? `?reassign_to=${reassignTo}` : ""}`),
  );
