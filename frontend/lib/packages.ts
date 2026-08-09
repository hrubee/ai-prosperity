// Single source of truth for the subscription packages.
// Mirrored server-side by the entitlement gate in the execution worker (Phase 3).

export type PackageId = "starter" | "growth" | "pro";

export interface Package {
  id: PackageId;
  name: string;
  pricePerMonthInr: number;
  tagline: string;
  // Human-readable entitlement; the worker enforces the machine rule.
  entitlement: string;
  features: string[];
  highlighted?: boolean;
  // Machine rule consumed by the entitlement gate.
  rule:
    | { kind: "trades_per_week"; max: number }
    | { kind: "trading_days_per_week"; max: number }
    | { kind: "unlimited" };
}

export const PACKAGES: Package[] = [
  {
    id: "starter",
    name: "Starter",
    pricePerMonthInr: 5000,
    tagline: "Dip your toe in.",
    entitlement: "1 trade per week",
    features: [
      "1 AI signal executed per week",
      "2% risk-per-trade sizing on your equity",
      "Auto stop-loss on every position",
      "Live dashboard & PnL",
    ],
    rule: { kind: "trades_per_week", max: 1 },
  },
  {
    id: "growth",
    name: "Growth",
    pricePerMonthInr: 10000,
    tagline: "Catch more of the move.",
    entitlement: "Up to 2 trading days / week",
    features: [
      "Signals execute on up to 2 days each week",
      "2% risk-per-trade sizing on your equity",
      "Auto stop-loss on every position",
      "Live dashboard, PnL & signal feed",
      "Priority support",
    ],
    highlighted: true,
    rule: { kind: "trading_days_per_week", max: 2 },
  },
  {
    id: "pro",
    name: "Pro",
    pricePerMonthInr: 20000,
    tagline: "Every signal, every day.",
    entitlement: "Unlimited — all signals, all days",
    features: [
      "Every AI signal executed, all days",
      "2% risk-per-trade sizing on your equity",
      "Auto stop-loss on every position",
      "Live dashboard, PnL & signal feed",
      "Priority support",
    ],
    rule: { kind: "unlimited" },
  },
];

export const formatInr = (n: number) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(n);
