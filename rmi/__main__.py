"""
Point d'entrée : python -m rmi

Lance l'application GUI SpraySpy.
"""

import logging
from .gui_app import main

# Configurer le logging console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

if __name__ == "__main__":
    main()
