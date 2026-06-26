import { CFG, KV_KEYS } from '../config/constants.js';
export class CircuitBreakerService {
  constructor(kv) { this.kv = kv; this.LOCK_KEY = 'cycle_lock'; this.LOCK_TTL = 60; }
  async acquireLock() {
    const now = Date.now();
    return await this.kv.put(this.LOCK_KEY, JSON.stringify({ holder: 'orchestrator', ts: now }), {
      expirationTtl: this.LOCK_TTL, condition: { notExists: true }
    });
  }
  async releaseLock() { await this.kv.delete(this.LOCK_KEY); }
  async getState() {
    const defaultState = { failures: 0, paused: false, pausedAt: null, pauseReason: null, manualOverride: false, failureHistory: [] };
    return (await this.kv.getJSON(KV_KEYS.CIRCUIT)) || defaultState;
  }
  async _saveState(s) { await this.kv.putJSON(KV_KEYS.CIRCUIT, s); }
  async recordFailure(reason, isFatal = false) {
    const state = await this.getState();
    if (state.manualOverride) return state;
    const now = Date.now();
    state.failureHistory = (state.failureHistory || []).filter(f => (now - f.timestamp) < CFG.CIRCUIT_WINDOW_MS);
    state.failureHistory.push({ timestamp: now, reason, isFatal });
    const recent = state.failureHistory.length;
    if (isFatal || recent >= CFG.CIRCUIT_FAIL_LIMIT) {
      state.paused = true; state.pausedAt = now;
      state.pauseReason = isFatal ? `Fatal: ${reason}` : `${recent} failures in ${CFG.CIRCUIT_WINDOW_MS/60000}min — last: ${reason}`;
    }
    await this._saveState(state);
    return state;
  }
  async recordSuccess() {
    const state = await this.getState();
    if (state.manualOverride) return state;
    state.failureHistory = []; state.failures = 0;
    if (state.paused && !state.manualOverride) { state.paused = false; state.pausedAt = null; state.pauseReason = null; }
    await this._saveState(state);
    return state;
  }
  async isOpen() {
    const state = await this.getState();
    if (!state.paused) return false;
    if (!state.manualOverride) {
      const elapsed = Date.now() - (state.pausedAt || 0);
      if (elapsed > CFG.CIRCUIT_RESET_MS) { await this.reset('auto_recovery'); return false; }
    }
    return state.paused;
  }
  async reset(reason = 'manual') {
    const state = await this.getState();
    state.failures = 0; state.paused = false; state.pausedAt = null; state.pauseReason = null;
    state.manualOverride = false; state.failureHistory = [];
    await this._saveState(state);
    return state;
  }
  async setManualOverride(enable) {
    const state = await this.getState();
    state.manualOverride = enable;
    await this._saveState(state);
    return state;
  }
}
