"""Renderiza el flyer HTML a PNG 1080x1350 listo para WhatsApp.

Uso:
    python scripts/marketing/exportar_flyer.py

Reutiliza el Selenium ya instalado en requirements.txt. No agrega dependencias.
"""

from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


REPO_ROOT = Path(__file__).resolve().parents[2]
HTML_PATH = REPO_ROOT / "static" / "marketing" / "flyer-whatsapp-er.html"
PNG_PATH = REPO_ROOT / "static" / "marketing" / "flyer-whatsapp-er.png"

WIDTH, HEIGHT = 1080, 1350


def render():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--hide-scrollbars")
    options.add_argument(f"--window-size={WIDTH},{HEIGHT}")
    options.add_argument("--force-device-scale-factor=1")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.set_window_size(WIDTH, HEIGHT)
        driver.get(HTML_PATH.as_uri())

        # Esperar a que las webfonts terminen de cargar antes del screenshot.
        driver.execute_async_script(
            "var done = arguments[arguments.length - 1];"
            "if (document.fonts && document.fonts.ready) {"
            "  document.fonts.ready.then(() => setTimeout(done, 250));"
            "} else { setTimeout(done, 1000); }"
        )

        flyer = driver.find_element("css selector", ".flyer")
        flyer.screenshot(str(PNG_PATH))
        print(f"PNG generado: {PNG_PATH}")
    finally:
        driver.quit()


if __name__ == "__main__":
    render()
