// Single source of truth for AI Prosperity business model.
// Model: 40-60 Profit Split (Zero Upfront / Subscription Fees)
// 60% Client Equity Growth | 40% Platform Performance Share

export interface ProfitSplitModel {
  clientSharePercent: number; // 60
  platformSharePercent: number; // 40
  upfrontFeeInr: number; // 0
  billingFrequency: string; // "Performance-based on Net Realized Profit"
  features: string[];
}

export const PROFIT_SPLIT_MODEL: ProfitSplitModel = {
  clientSharePercent: 60,
  platformSharePercent: 40,
  upfrontFeeInr: 0,
  billingFrequency: "Performance-Based (No Monthly Subscription)",
  features: [
    "60% Profit Retained by You (Client)",
    "40% Performance Share on Net Realized Profits",
    "₹0 Monthly Subscription & Zero Upfront Fee",
    "High Water Mark Protection (Pay only when profitable)",
    "Full Automated Copy Trading on Tradejini & CoinDCX",
    "Strict 1% Risk-per-trade Sizing & Auto Stop-Loss",
    "Real-Time PnL Dashboard & Ledger Transparency",
  ],
};

export const formatInr = (n: number) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(n);

export const calculateProfitSplit = (netProfitInr: number) => {
  if (netProfitInr <= 0) {
    return { clientAmount: netProfitInr, platformAmount: 0 };
  }
  const clientAmount = netProfitInr * (PROFIT_SPLIT_MODEL.clientSharePercent / 100);
  const platformAmount = netProfitInr * (PROFIT_SPLIT_MODEL.platformSharePercent / 100);
  return { clientAmount, platformAmount };
};
