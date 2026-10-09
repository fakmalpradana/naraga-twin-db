import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT = BASE_DIR.parent
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
ALLOWED_ORIGINS = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]
WRITE_API_TOKEN = os.environ.get("WRITE_API_TOKEN", "")   # empty = write API disabled (POST/PATCH/DELETE return 503)
CITYDB_TOOL = os.environ.get("CITYDB_TOOL", "/opt/citydb-tool/citydb")
ADMIN_EDIT_ENABLED = os.environ.get("ADMIN_EDIT_ENABLED", "0") == "1"   # 0 = read-only admin
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")           # behind Railway/Caddy
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = not DEBUG and os.environ.get("INSECURE_COOKIES", "0") != "1"

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "catalog",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "catalog.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "catalog.middleware.AppUserMiddleware",   # must come after authentication
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "DIRS": [],
              "OPTIONS": {"context_processors": [
                  "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"

_u = urlparse(os.environ.get("DATABASE_URL", "postgres://postgres:change-me@localhost:55432/postgres"))
_base = {"ENGINE": "django.db.backends.postgresql", "NAME": (_u.path or "/postgres").lstrip("/"),
         "HOST": _u.hostname, "PORT": _u.port or 5432}
DATABASES = {
    # admin/editing: role twin_app (Django groups decide who may do what)
    "default": {**_base, "USER": "twin_app", "PASSWORD": os.environ.get("APP_DB_PASSWORD", ""),
                "OPTIONS": {"options": "-c search_path=app,catalog,citydb,public"}},
    # public read-only API: role twin_api (SELECT only)
    "ro": {**_base, "USER": "twin_api", "PASSWORD": os.environ.get("API_DB_PASSWORD", ""),
           "OPTIONS": {"options": "-c search_path=catalog,citydb,public -c default_transaction_read_only=on"}},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
                             "OPTIONS": {"min_length": 8}}]
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}}
LOGIN_URL = "/admin/login/"
