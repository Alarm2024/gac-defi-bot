import os
import asyncio
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("GuardianBot")

# Initialize Groq Client
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    logger.error("GROQ_API_KEY environment variable is not set.")
    exit(1)

client = Groq(api_key=GROQ_API_KEY)

async def get_groq_response(text: str) -> str:
    try:
        completion = client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are a helpful and intelligent assistant."},
                {"role": "user", "content": text}
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq API Error: {e}")
        return "Sorry, I encountered an error processing your request."

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    logger.info(f"Received message: {user_text}")
    
    # Send 'thinking' status
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    reply = await get_groq_response(user_text)
    
    logger.info(f"Sending response: {reply}")
    await update.message.reply_text(reply)

async def main():
    token = os.environ.get("GUARDIAN_TELEGRAM_TOKEN")
    if not token:
        logger.error("GUARDIAN_TELEGRAM_TOKEN environment variable is not set.")
        return

    # Build the application
    app = ApplicationBuilder().token(token).build()

    # Add handler
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))

    logger.info("Starting bot...")
    await app.initialize()
    await app.start()
    
    # Important: Delete webhook to avoid conflict
    await app.bot.delete_webhook(drop_pending_updates=True)
    
    await app.updater.start_polling()
    
    print("DEPLOYMENT SUCCESSFUL - BOT IS READY TO CHAT")
    logger.info("BOT IS LIVE AND LISTENING")

    # Keep running
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown initiated.")
