export class TelegramService {
  constructor(env) { this.env = env; this.token = env.TELEGRAM_BOT_TOKEN; }
  async send(chatId, text, parseMode = 'Markdown', extra = {}) {
    if (!this.token || !chatId) return;
    try {
      const payload = { chat_id: chatId, text, parse_mode: parseMode, ...extra };
      const resp = await fetch(`https://api.telegram.org/bot${this.token}/sendMessage`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      return resp.json();
    } catch (e) { console.error('tgSend error:', e.message); }
  }
  async answerCallbackQuery(callbackQueryId, text = '', showAlert = false) {
    if (!this.token || !callbackQueryId) return;
    try {
      const resp = await fetch(`https://api.telegram.org/bot${this.token}/answerCallbackQuery`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ callback_query_id: callbackQueryId, text, show_alert: showAlert })
      });
      return resp.json();
    } catch (e) { console.error('answerCallbackQuery error:', e.message); }
  }
  async sendDocument(chatId, jsonData, filename = 'report.json', caption = '') {
    if (!this.token || !chatId) return;
    try {
      const blob = new Blob([jsonData], { type: 'application/json' });
      const formData = new FormData();
      formData.append('chat_id', chatId.toString());
      formData.append('document', blob, filename);
      if (caption) formData.append('caption', caption);
      formData.append('parse_mode', 'Markdown');
      const resp = await fetch(`https://api.telegram.org/bot${this.token}/sendDocument`, {
        method: 'POST', body: formData
      });
      return resp.json();
    } catch (e) { console.error('sendDocument error:', e.message); }
  }
}
