import os
import sys

CURRENT_DIR = os.path.dirname(__file__)
SERVICE_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
if SERVICE_ROOT not in sys.path:
    sys.path.insert(0, SERVICE_ROOT)
