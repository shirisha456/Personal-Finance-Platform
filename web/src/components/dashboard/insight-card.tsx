"use client";

import { useState } from "react";
import { ChevronLeftIcon, ChevronRightIcon, SparklesIcon } from "lucide-react";

import { useGenerateInsight, useLatestInsight } from "@/hooks/use-insights";
import { apiErrorMessage } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function monthStartIso(offsetFromCurrent: number): string {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() + offsetFromCurrent, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

function monthLabel(iso: string): string {
  const [year, month] = iso.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

export function InsightCard() {
  const [monthOffset, setMonthOffset] = useState(0);
  const periodStart = monthStartIso(monthOffset);

  const { data: insight, isLoading } = useLatestInsight();
  const generateInsight = useGenerateInsight();
  const [error, setError] = useState<string | null>(null);

  async function handleGenerate() {
    setError(null);
    try {
      await generateInsight.mutateAsync({ period_start: periodStart });
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't generate an insight."));
    }
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <SparklesIcon className="size-4" />
            Monthly insight
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-4 w-full" />
          <Skeleton className="mt-2 h-4 w-3/4" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <SparklesIcon className="size-4" />
          Monthly insight
        </CardTitle>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="icon-sm" onClick={() => setMonthOffset((o) => o - 1)}>
            <ChevronLeftIcon />
            <span className="sr-only">Previous month</span>
          </Button>
          <span className="min-w-32 text-center text-xs text-muted-foreground">{monthLabel(periodStart)}</span>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => setMonthOffset((o) => o + 1)}
            disabled={monthOffset >= 0}
          >
            <ChevronRightIcon />
            <span className="sr-only">Next month</span>
          </Button>
          <Button size="sm" variant="outline" onClick={() => void handleGenerate()} disabled={generateInsight.isPending}>
            {generateInsight.isPending ? "Generating…" : "Generate"}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : insight ? (
          <p className="text-sm text-foreground">{insight.summary}</p>
        ) : (
          <p className="text-sm text-muted-foreground">
            No insight yet for {monthLabel(periodStart)} — click Generate for a summary of that month&rsquo;s
            spending.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
