"""Renderiza los flyers HTML a PNG listos para enviar.

Genera dos piezas:
  - flyer-whatsapp-er.png  (1080x1350, proporción 4:5 para chat de WhatsApp)
  - flyer-story-er.png     (1080x1920, proporción 9:16 para Stories de WhatsApp/Instagram)

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
MARKETING_DIR = REPO_ROOT / "static" / "marketing"

PIEZAS = [
    ("flyer-whatsapp-er", 1080, 1350),
    ("flyer-story-er", 1080, 1920),
]


def _crear_driver(width: int, height: int):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--hide-scrollbars")
    options.add_argument(f"--window-size={width},{height}")
    options.add_argument("--force-device-scale-factor=1")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def render(nombre: str, width: int, height: int):
    html_path = MARKETING_DIR / f"{nombre}.html"
    png_path = MARKETING_DIR / f"{nombre}.png"

    driver = _crear_driver(width, height)
    try:
        driver.set_window_size(width, height)
        driver.get(html_path.as_uri())

        driver.execute_async_script(
            "var done = arguments[arguments.length - 1];"
            "if (document.fonts && document.fonts.ready) {"
            "  document.fonts.ready.then(() => setTimeout(done, 250));"
            "} else { setTimeout(done, 1000); }"
        )

        flyer = driver.find_element("css selector", ".flyer")
        flyer.screenshot(str(png_path))
        print(f"PNG generado: {png_path}  ({width}x{height})")
    finally:
        driver.quit()


if __name__ == "__main__":
    for nombre, w, h in PIEZAS:
        render(nombre, w, h)
