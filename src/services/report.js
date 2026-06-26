// 🪬🧿✝️  ReportService – v17.1
// ─────────────────────────────────────────────────────────────────────────────
// Provides:
//   • logPayoutAttempt(data)  — appends a payout audit entry to KV
//   • getPayoutLog()          — returns full payout history
//   • generateSummary()       — returns a structured P&L snapshot object
//
// Used by:
//   • index.js → handlePayout()  (logPayoutAttempt, sendDocument)
//   • Any future /report command
//
// KV schema
//   report:payout_log  — JSON array of payout audit entries, capped at LOG_CAP
//   report:summary     — last-written P&L summary (overwritten each time)
// ─────────────────────────────────────────────────────────────────────────────

const PAYOUT_LOG_KEY = 'report:payout_log';
const SUMMARY_KEY    = 'report:summary';
const LOG_CAP        = 100;   // max payout log entries kept in KV

export class ReportService {
  /**
   * @param {KVService} kv  — an instance of KVService (exposes getJSON / putJSON)
   */
  constructor(kv) {
    this.kv = kv;
  }

  // ── logPayoutAttempt ───────────────────────────────────────────────────────
  // Appends one payout audit entry to the ring-buffer in KV.
  //
  // @param {object} data
  //   Required: status  (string, e.g. 'SUCCESS' | 'AUTH_FAILED' | 'ZERO_PAYOUT' |
  //                       'NO_TRADES' | 'TX_REVERTED' | 'EXECUTION_ERROR')
  //   Optional: amount  (number, USD)
  //             txHash  (string)
  //             recipient (string)
  //             gasUsed (string)
  //             error   (string)

  async logPayoutAttempt(data = {}) {
    let log = [];
    try {
      log = (await this.kv.getJSON(PAYOUT_LOG_KEY)) ?? [];
      if (!Array.isArray(log)) log = [];
    } catch {
      log = [];
    }

    const entry = {
      ts       : new Date().toISOString(),
      status   : data.status   ?? 'UNKNOWN',
      amount   : data.amount   ?? null,
      txHash   : data.txHash   ?? null,
      recipient: data.recipient ?? null,
      gasUsed  : data.gasUsed  ?? null,
      error    : data.error    ?? null,
    };

    log.push(entry);

    // Keep the ring-buffer bounded
    if (log.length > LOG_CAP) {
      log = log.slice(log.length - LOG_CAP);
    }

    await this.kv.putJSON(PAYOUT_LOG_KEY, log);
    return entry;
  }

  // ── getPayoutLog ───────────────────────────────────────────────────────────
  // Returns the full payout audit log (newest last).

  async getPayoutLog() {
    try {
      const log = await this.kv.getJSON(PAYOUT_LOG_KEY);
      return Array.isArray(log) ? log : [];
    } catch {
      return [];
    }
  }

  // ── generateSummary ────────────────────────────────────────────────────────
  // Reads ledger keys from KV and builds a P&L summary object.
  // The caller supplies the raw KV values; this method is intentionally
  // stateless so it can be unit-tested without a live KV binding.
  //
  // @param {object} ledger  { gross, loanFees, gasDebt, tradeCount, adminFeePct }
  // @returns {object}       summary with all derived fields

  generateSummary({ gross = 0, loanFees = 0, gasDebt = 0, tradeCount = 0, adminFeePct = 0 } = {}) {
    const safeNum = (v) => { const n = parseFloat(v); return isNaN(n) || !isFinite(n) ? 0 : n; };

    const g   = safeNum(gross);
    const lf  = safeNum(loanFees);
    const gd  = safeNum(gasDebt);
    const ap  = safeNum(adminFeePct);
    const af  = g * (ap / 100);
    const net = g - lf - gd;
    const payout = net - af;

    return {
      ts          : new Date().toISOString(),
      tradeCount,
      grossProfit : g,
      loanFees    : lf,
      gasDebt     : gd,
      netPnL      : net,
      adminFee    : af,
      adminFeePct : ap,
      payoutReady : payout,
    };
  }

  // ── writeSummary ───────────────────────────────────────────────────────────
  // Convenience: generate + persist a summary to KV.

  async writeSummary(ledger = {}) {
    const summary = this.generateSummary(ledger);
    await this.kv.putJSON(SUMMARY_KEY, summary);
    return summary;
  }

  // ── getSummary ─────────────────────────────────────────────────────────────

  async getSummary() {
    try {
      return await this.kv.getJSON(SUMMARY_KEY);
    } catch {
      return null;
    }
  }
}
