import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DJANGO_DEBUG', '0') == '1'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    raise ImproperlyConfigured('DJANGO_SECRET_KEY muss außerhalb des Quellcodes gesetzt werden.')
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
CSRF_TRUSTED_ORIGINS = [x for x in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if x]
INSTALLED_APPS = ['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','django.contrib.postgres','axes','tracker']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware','axes.middleware.AxesMiddleware']
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages']}}]
DATABASES = {'default': {'ENGINE':'django.db.backends.postgresql','NAME':os.getenv('PGDATABASE','clockin2'),'USER':os.getenv('PGUSER','clockin2'),'PASSWORD':os.getenv('PGPASSWORD',''),'HOST':os.getenv('PGHOST','127.0.0.1'),'PORT':os.getenv('PGPORT','5432'),'CONN_MAX_AGE':60}}
AUTH_PASSWORD_VALIDATORS = [{'NAME':'django.contrib.auth.password_validation.'+n} for n in ['UserAttributeSimilarityValidator','MinimumLengthValidator','CommonPasswordValidator','NumericPasswordValidator']]
AUTHENTICATION_BACKENDS = ['axes.backends.AxesStandaloneBackend','django.contrib.auth.backends.ModelBackend']
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1
AXES_LOCKOUT_PARAMETERS = [['username', 'ip_address']]
AXES_LOCKOUT_TEMPLATE = 'registration/locked.html'
LANGUAGE_CODE = 'de'
TIME_ZONE = 'Europe/Berlin'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR/'static']
STATIC_ROOT = BASE_DIR/'staticfiles'
MEDIA_ROOT = Path(os.getenv('UPLOAD_ROOT',str(BASE_DIR/'private_uploads')))
DATA_UPLOAD_MAX_MEMORY_SIZE = 12*1024*1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2*1024*1024
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/anmeldung/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = LOGIN_URL
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO','https')
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND','django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST','')
EMAIL_PORT = int(os.getenv('EMAIL_PORT','587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER','')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD','')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS','1') == '1'
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL','0') == '1'
EMAIL_TIMEOUT = 30
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL','')
OCR_LANGUAGE = os.getenv('OCR_LANGUAGE','deu')

OCR_TESSDATA_DIR = os.getenv('OCR_TESSDATA_DIR','')
