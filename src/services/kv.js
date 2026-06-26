export class KVService {
  constructor(ns) { this.ns = ns; }
  async get(k, opts) { return this.ns.get(k, opts); }
  async put(k, v, opts) { return this.ns.put(k, v, opts); }
  async delete(k) { return this.ns.delete(k); }
  async getJSON(k) { return this.get(k, { type: 'json' }); }
  async putJSON(k, o) { return this.put(k, JSON.stringify(o)); }
}
