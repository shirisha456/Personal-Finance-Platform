"use client";

import { useState } from "react";
import { SearchIcon, XIcon } from "lucide-react";

import { useAccounts } from "@/hooks/use-accounts";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useCreateHolding, useSearchSecurities } from "@/hooks/use-investments";
import { apiErrorMessage } from "@/lib/api-client";
import type { SymbolSearchResult } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export function AddHoldingDialog() {
  const { data: accounts } = useAccounts();
  const createHolding = useCreateHolding();
  const investmentAccounts = (accounts?.items ?? []).filter((a) => a.type === "investment");

  const [open, setOpen] = useState(false);
  const [accountId, setAccountId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [costBasis, setCostBasis] = useState("");
  const [error, setError] = useState<string | null>(null);

  // The one thing that changed: a holding can no longer be created from
  // two disconnected inputs where typing garbage into "Symbol" silently
  // bypassed search entirely. Now there is exactly one selected security
  // at a time — set either by picking a real search result, or, only if
  // the user explicitly opts in, by typing one in manually.
  const [selected, setSelected] = useState<SymbolSearchResult | null>(null);
  const [manualEntry, setManualEntry] = useState(false);
  const [manualSymbol, setManualSymbol] = useState("");
  const [manualName, setManualName] = useState("");

  const [searchQuery, setSearchQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);
  const debouncedQuery = useDebouncedValue(searchQuery, 300);
  const { data: searchResults, isFetching: isSearching, isError: searchUnavailable } =
    useSearchSecurities(debouncedQuery);

  const showResults = searchFocused && debouncedQuery.trim().length > 0;

  function resetSecuritySelection() {
    setSelected(null);
    setManualEntry(false);
    setManualSymbol("");
    setManualName("");
    setSearchQuery("");
  }

  function selectResult(result: SymbolSearchResult) {
    setSelected(result);
    setSearchQuery("");
    setSearchFocused(false);
  }

  const canSubmit =
    !!accountId && (selected !== null || (manualEntry && manualSymbol.trim().length > 0));

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await createHolding.mutateAsync({
        account_id: accountId,
        symbol: selected ? selected.symbol : manualSymbol.trim(),
        name: selected ? selected.name : manualName.trim() || undefined,
        quantity: Number.parseFloat(quantity) || 0,
        cost_basis_minor: Math.round((Number.parseFloat(costBasis) || 0) * 100),
      });
      setOpen(false);
      resetSecuritySelection();
      setQuantity("");
      setCostBasis("");
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't add that holding."));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" />}>Add holding</DialogTrigger>
      {open && (
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add holding</DialogTitle>
          </DialogHeader>
          {investmentAccounts.length === 0 ? (
            <DialogDescription>
              You need an investment-type account first — add one from the Accounts page, then come back here.
            </DialogDescription>
          ) : (
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label>Account</Label>
                <Select value={accountId} onValueChange={(value) => setAccountId(value ?? "")}>
                  <SelectTrigger>
                    <SelectValue placeholder="Choose an account">
                      {() => investmentAccounts.find((a) => a.id === accountId)?.name}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {investmentAccounts.map((account) => (
                      <SelectItem key={account.id} value={account.id}>
                        {account.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label>Security</Label>

                {selected ? (
                  <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-sm">
                    <div>
                      <span className="font-medium">{selected.symbol}</span>{" "}
                      <span className="text-muted-foreground">{selected.name}</span>
                    </div>
                    <button
                      type="button"
                      onClick={resetSecuritySelection}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <XIcon className="size-4" />
                      <span className="sr-only">Clear selection</span>
                    </button>
                  </div>
                ) : manualEntry ? (
                  <div className="flex flex-col gap-2">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="flex flex-col gap-1.5">
                        <Label htmlFor="holding-manual-symbol">Symbol</Label>
                        <Input
                          id="holding-manual-symbol"
                          required
                          value={manualSymbol}
                          onChange={(e) => setManualSymbol(e.target.value.toUpperCase())}
                          placeholder="TSLA"
                        />
                      </div>
                      <div className="flex flex-col gap-1.5">
                        <Label htmlFor="holding-manual-name">Name (optional)</Label>
                        <Input
                          id="holding-manual-name"
                          value={manualName}
                          onChange={(e) => setManualName(e.target.value)}
                        />
                      </div>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Entering manually skips symbol validation — double check the ticker is correct.{" "}
                      <button type="button" className="underline" onClick={() => setManualEntry(false)}>
                        Search instead
                      </button>
                    </p>
                  </div>
                ) : (
                  <div className="relative">
                    <div className="relative">
                      <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        className="pl-8"
                        placeholder="Search by company name or symbol — Tesla, Apple, VTI…"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        onFocus={() => setSearchFocused(true)}
                        onBlur={() => setTimeout(() => setSearchFocused(false), 150)}
                      />
                    </div>
                    {showResults && (
                      <div className="absolute top-full z-10 mt-1 w-full rounded-lg border border-border bg-popover shadow-md">
                        {isSearching ? (
                          <p className="p-2.5 text-xs text-muted-foreground">Searching…</p>
                        ) : searchUnavailable ? (
                          <div className="p-2.5 text-xs text-muted-foreground">
                            Symbol search isn&rsquo;t available right now.{" "}
                            <button
                              type="button"
                              className="underline"
                              onMouseDown={(e) => e.preventDefault()}
                              onClick={() => setManualEntry(true)}
                            >
                              Enter it manually instead
                            </button>
                          </div>
                        ) : searchResults && searchResults.length > 0 ? (
                          <ul className="max-h-56 overflow-y-auto py-1">
                            {searchResults.map((result) => (
                              <li key={`${result.symbol}-${result.exchange}`}>
                                <button
                                  type="button"
                                  className="flex w-full flex-col items-start gap-0.5 px-2.5 py-1.5 text-left text-sm hover:bg-accent"
                                  onMouseDown={(e) => e.preventDefault()}
                                  onClick={() => selectResult(result)}
                                >
                                  <span className="font-medium">
                                    {result.symbol}{" "}
                                    <span className="font-normal text-muted-foreground">{result.exchange}</span>
                                  </span>
                                  <span className="text-xs text-muted-foreground">{result.name}</span>
                                </button>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <div className="p-2.5 text-xs text-muted-foreground">
                            No matches for &ldquo;{debouncedQuery}&rdquo;.{" "}
                            <button
                              type="button"
                              className="underline"
                              onMouseDown={(e) => e.preventDefault()}
                              onClick={() => setManualEntry(true)}
                            >
                              Enter it manually
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="holding-quantity">Quantity</Label>
                  <Input
                    id="holding-quantity"
                    type="number"
                    min="0.000001"
                    step="any"
                    required
                    value={quantity}
                    onChange={(e) => setQuantity(e.target.value)}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="holding-cost">Cost basis</Label>
                  <Input
                    id="holding-cost"
                    type="number"
                    min="0"
                    step="0.01"
                    required
                    value={costBasis}
                    onChange={(e) => setCostBasis(e.target.value)}
                  />
                </div>
              </div>
              {error && <p className="text-sm text-destructive">{error}</p>}
              <DialogFooter>
                <Button type="submit" disabled={createHolding.isPending || !canSubmit}>
                  {createHolding.isPending ? "Adding…" : "Add holding"}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      )}
    </Dialog>
  );
}
