import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { Holding, Page, Security, SymbolSearchResult, WatchlistItem } from "@/lib/types";

interface CreateHoldingInput {
  account_id: string;
  symbol: string;
  name?: string;
  quantity: number;
  cost_basis_minor: number;
}

interface AddWatchlistInput {
  symbol: string;
  name?: string;
}

export function useHoldings(limit = 50, offset = 0) {
  return useQuery({
    queryKey: ["holdings", limit, offset],
    queryFn: async () =>
      (await apiClient.get<Page<Holding>>("/api/v1/investments/holdings", { params: { limit, offset } })).data,
  });
}

export function useWatchlist() {
  return useQuery({
    queryKey: ["watchlist"],
    queryFn: async () => (await apiClient.get<WatchlistItem[]>("/api/v1/investments/watchlist")).data,
  });
}

export function useCreateHolding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: CreateHoldingInput) =>
      (await apiClient.post<Holding>("/api/v1/investments/holdings", input)).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["holdings"] }),
  });
}

export function useDeleteHolding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/investments/holdings/${id}`);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["holdings"] }),
  });
}

export function useAddToWatchlist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: AddWatchlistInput) =>
      (await apiClient.post<WatchlistItem>("/api/v1/investments/watchlist", input)).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["watchlist"] }),
  });
}

export function useRemoveFromWatchlist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/investments/watchlist/${id}`);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["watchlist"] }),
  });
}

export function useSearchSecurities(query: string) {
  return useQuery({
    queryKey: ["securities-search", query],
    queryFn: async () =>
      (await apiClient.get<SymbolSearchResult[]>("/api/v1/investments/securities/search", { params: { query } }))
        .data,
    enabled: query.trim().length > 0,
    // Market data being unconfigured (503) is an expected, quiet state
    // here — the user can still type a symbol manually — not a retry-
    // worthy transient failure.
    retry: false,
  });
}

export function useRefreshPrices() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.post<Security[]>("/api/v1/investments/prices/refresh")).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["holdings"] });
      void queryClient.invalidateQueries({ queryKey: ["watchlist"] });
    },
  });
}
