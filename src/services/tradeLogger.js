// 🪬🧿✝️  GARDEN ANGEL v16.3 – TRADE LOGGER
import { KV_KEYS } from '../config/constants.js';

export class TradeLoggerService {
  constructor(kv) { this.kv = kv; }

  async log(entry) {
    const logs = (await this.kv.getJSON(KV_KEYS.TRADE_LOG)) || [];
    const logData = { ts: new Date().toISOString(), ...entry };
    logs.push(logData);
    await this.kv.putJSON(KV_KEYS.TRADE_LOG, logs.slice(-50));
    console.log(`📊 Trade logged: ${entry.signal ?? entry.status} | Gwei: ${entry.gwei ?? 'N/A'}`);
  }

  async logFailed(entry) {
    const logs = (await this.kv.getJSON(KV_KEYS.FAILED_LOG)) || [];
    logs.push({ ts: new Date().toISOString(), ...entry });
    await this.kv.putJSON(KV_KEYS.FAILED_LOG, logs.slice(-50));
    console.log(`❌ Failed: ${entry.reason ?? 'unknown'}`);
  }

  async getAll() {
    return (await this.kv.getJSON(KV_KEYS.TRADE_LOG)) || [];
  }

  async getRecent(n = 10) {
    const logs = (await this.kv.getJSON(KV_KEYS.TRADE_LOG)) || [];
    return logs.slice(-n).reverse();
  }
}
