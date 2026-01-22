import os
from pathlib import Path
import environ
import dj_database_url


BASE_DIR = Path(__file__).resolve().parent.parent



env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env') if os.path.exists('.env') else None
DEBUG = env('DEBUG', default=True)
SECRET_KEY = env('SECRET_KEY', default='changeme')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
ALLOWED_HOSTS = [
    'collection.rarehuntertcg.com',
    'www.collection.rarehuntertcg.com',
    'rarehuntertcgcollection-production.up.railway.app',  # temporary
]

CSRF_TRUSTED_ORIGINS = [
    'https://collection.rarehuntertcg.com',
    'https://www.collection.rarehuntertcg.com',
    'https://rarehuntertcgcollection-production.up.railway.app',  # temporary
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'collection',
]

MIDDLEWARE = [
    'whitenoise.middleware.WhiteNoiseMiddleware',  # add near the top, after SecurityMiddleware
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'ygostore.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'ygostore.wsgi.application'

DATABASES = {
    'default': dj_database_url.config(
        default='sqlite:///' + str(BASE_DIR / 'db.sqlite3'),
        conn_max_age=600,
        ssl_require=True  # important for Neon
    )
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = Path("/app/uploads")
MEDIA_URL = '/media/'

# Stripe configuration
STRIPE_SECRET_KEY = env('STRIPE_SECRET_KEY', default='')
STRIPE_WEBHOOK_SECRET = env('STRIPE_WEBHOOK_SECRET', default='')
BASE_URL = env('BASE_URL', default='https://collection.rarehuntertcg.com')
