"""Independent single-workstation edition. Never serves the LAN."""
from .settings import *
PORTABLE = True
DATA_DIR = Path(os.getenv('PORTABLE_DATA_DIR',str(BASE_DIR.parent/'Daten')))
DATA_DIR.mkdir(parents=True,exist_ok=True)
DATABASES = {'default': {'ENGINE':'django.db.backends.sqlite3','NAME':DATA_DIR/'zeiterfassung.sqlite3','OPTIONS':{'timeout':30,'transaction_mode':'IMMEDIATE'},'TEST':{'NAME':DATA_DIR/'test_zeiterfassung.sqlite3'}}}
MIGRATION_MODULES = {'tracker':'tracker.migrations_portable'}
DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1','localhost']
CSRF_TRUSTED_ORIGINS = []
MEDIA_ROOT = DATA_DIR/'Plaene'
STATIC_ROOT = BASE_DIR/'staticfiles'
MIDDLEWARE.insert(1,'whitenoise.middleware.WhiteNoiseMiddleware')
# Only loopback HTTP between the browser and this local process; no remote listener.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_PROXY_SSL_HEADER = None
