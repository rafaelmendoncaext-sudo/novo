"""
whatsapp_app.py — shim de compatibilidade
==========================================
A aplicação real está em `app.py` (objeto `app`). Alguns serviços no Render
ficaram configurados com o Start Command antigo `gunicorn whatsapp_app:app`
(nome usado em outro projeto). Este módulo apenas reexporta o `app` para que
esse comando continue funcionando sem precisar mexer no dashboard.

O ideal, porém, é usar o Start Command correto:
    gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
"""

from app import app  # noqa: F401  (reexporta o Flask app)
