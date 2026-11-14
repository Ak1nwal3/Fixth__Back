# Fixth backend

Django API for Fixth. The canonical API namespace is `/api/v1/`.

## Local setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Create `.env` next to `manage.py` from `../.env.example`.

## Transactional email

Email is delivered through Django's standard SMTP settings.

- Provider: **SendByte SMTP**
- Local fallback: Django's console backend when `EMAIL_HOST` is unset
- Celery: optional; `CELERY_TASK_ALWAYS_EAGER=true` sends inline locally

See [`../docs/sendbyte-smtp.md`](../docs/sendbyte-smtp.md).
