import logging
from playwright.async_api import async_playwright  # Usamos la versión asíncrona de Playwright
from bs4 import BeautifulSoup
import datetime
import os
from telegram import Update, ChatPermissions
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_IDS = [5032964793]
GENERAL_CHAT_ID = "-2421748184"      # Grupo principal D.N.A TV
GROUP_ID = "-2421748184"             # Igual que GENERAL_CHAT_ID
CANAL_EVENTOS_ID = "-1002421748184"  # Canal EVENTOS DEPORTIVOS

CARTELERA_URL = "https://www.emol.com/movil/deportes/carteleradirecttv/index.aspx"
TZ = datetime.timezone.utc

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- Scraping Cartelera ---
async def scrape_cartelera():
    try:
        async with async_playwright() as p:
            # Lanzar el navegador en modo "headless" (sin interfaz gráfica)
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(CARTELERA_URL)

            # Aumentamos el tiempo de espera y esperamos por un selector diferente
            await page.wait_for_selector("div.cartelera_fecha", timeout=60000)  # Esperar hasta 60 segundos

            # Obtener el contenido de la página después de cargar JavaScript
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            eventos = []
            for bloque_fecha in soup.find_all("div", class_="cartelera_fecha"):
                fecha = bloque_fecha.get_text(strip=True)
                bloque_eventos = bloque_fecha.find_next_sibling("div", class_="cartelera_eventos")
                if not bloque_eventos:
                    continue
                for evento in bloque_eventos.find_all("div", class_="cartelera_evento"):
                    hora = evento.find("div", class_="cartelera_hora")
                    nombre = evento.find("div", class_="cartelera_nombre")
                    logo_img = evento.find("img")
                    eventos.append({
                        "fecha": fecha,
                        "hora": hora.get_text(strip=True) if hora else "",
                        "nombre": nombre.get_text(strip=True) if nombre else "",
                        "logo": logo_img['src'] if logo_img and logo_img.has_attr('src') else ""
                    })

            await browser.close()  # Cerrar el navegador al terminar
            return eventos

    except Exception as e:
        logging.error(f"Error al obtener la cartelera: {e}")
        return []

# --- Envío diario de eventos al canal EVENTOS DEPORTIVOS ---
async def enviar_eventos_diarios(context: ContextTypes.DEFAULT_TYPE):
    eventos = await scrape_cartelera()  # Llamamos a la función asíncrona
    if not eventos:
        await context.bot.send_message(chat_id=CANAL_EVENTOS_ID, text="No hay eventos deportivos programados para hoy y mañana.")
        return
    for evento in eventos:
        texto = f"📅 *{evento['fecha']}*\n*{evento['hora']}*\n_{evento['nombre']}_"
        if evento['logo']:
            await context.bot.send_photo(chat_id=CANAL_EVENTOS_ID, photo=evento['logo'], caption=texto, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=CANAL_EVENTOS_ID, text=texto, parse_mode="Markdown")

# --- Comando /cartelera manual ---
async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eventos = await scrape_cartelera()  # Llamamos a la función asíncrona
    if not eventos:
        await update.message.reply_text("No hay eventos deportivos programados para hoy y mañana.")
        return
    for evento in eventos:
        texto = f"📅 *{evento['fecha']}*\n*{evento['hora']}*\n_{evento['nombre']}_"
        if evento['logo']:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=evento['logo'], caption=texto, parse_mode="Markdown")
        else:
            await update.message.reply_text(texto, parse_mode="Markdown")

# --- MAIN ---
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Comandos
    application.add_handler(CommandHandler("cartelera", cartelera))

    # Jobs automáticos
    application.job_queue.run_daily(
        lambda context: enviar_eventos_diarios(context),
        time=datetime.time(hour=10, minute=0, tzinfo=TZ),
        name="eventos_diarios"
    )

    # --- WEBHOOK ---
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

if __name__ == "__main__":
    main()
