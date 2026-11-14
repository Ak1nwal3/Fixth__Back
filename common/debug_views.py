"""
Temporary SMTP connectivity probe for diagnosing outbound email from a
deployment (Render free tiers have no shell to run these checks manually).

GET /api/v1/debug-smtp/?token=<DEBUG_SMTP_TOKEN>

Reports DNS resolution plus per-host/per-port connect results for the mail
hosts in play, and the effectively-applied email settings (secret values
masked). Remove this module and its url entry once email delivery works.
"""

import os
import socket
from time import perf_counter

from django.conf import settings
from django.http import JsonResponse
from django.views import View

SMTP_TOKEN = os.getenv("DEBUG_SMTP_TOKEN", "fixth-smtp-probe-8217")

TARGETS = [
    ("configured EMAIL_HOST", settings.EMAIL_HOST, settings.EMAIL_PORT),
    ("SendByte SMTP TLS", "smtp.sendbyte.africa", 587),
    ("SendByte SMTP SSL", "smtp.sendbyte.africa", 465),
    ("SendByte SMTP alternate TLS", "smtp.sendbyte.africa", 2525),
]


def _probe_connect(host: str, port: int, timeout: float = 5.0) -> dict:
    if not host:
        return {"skipped": "no host configured", "port": port}
    result: dict = {"host": host, "port": port, "ok": False}
    started = perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        result["resolved"] = sorted({info[4][0] for info in infos})
    except Exception as exc:  # noqa: BLE001 - diagnostic view
        result["dns_error"] = f"{type(exc).__name__}: {exc}"
        result["elapsed_ms"] = round((perf_counter() - started) * 1000)
        return result
    for address in result.get("resolved") or []:
        try:
            started_one = perf_counter()
            sock = socket.create_connection((address, port), timeout=timeout)
            sock.close()
            result["ok"] = True
            result.setdefault("connected", []).append(
                {"address": address, "ms": round((perf_counter() - started_one) * 1000)}
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic view
            result.setdefault("failed", []).append(
                {
                    "address": address,
                    "error": f"{type(exc).__name__}: {exc}",
                    "ms": round((perf_counter() - started_one) * 1000),
                }
            )
    result["elapsed_ms"] = round((perf_counter() - started) * 1000)
    return result


def _mask(value: str) -> str:
    if not value:
        return ""
    return f"{value[:4]}...{value[-2:]}"


class SmtpDebugView(View):
    def get(self, request):
        token = request.GET.get("token", "")
        if not SMTP_TOKEN or token != SMTP_TOKEN:
            return JsonResponse({"detail": "Forbidden."}, status=403)

        email_settings = {
            "EMAIL_BACKEND": settings.EMAIL_BACKEND,
            "EMAIL_HOST": _mask(settings.EMAIL_HOST),
            "EMAIL_PORT": settings.EMAIL_PORT,
            "EMAIL_USE_TLS": settings.EMAIL_USE_TLS,
            "EMAIL_USE_SSL": settings.EMAIL_USE_SSL,
            "EMAIL_TIMEOUT": settings.EMAIL_TIMEOUT,
            "EMAIL_HOST_USER": _mask(settings.EMAIL_HOST_USER),
            "EMAIL_HOST_PASSWORD": "set" if settings.EMAIL_HOST_PASSWORD else "UNSET",
            "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
        }

        return JsonResponse(
            {
                "settings": email_settings,
                "connectivity": [
                    _probe_connect(target[1], target[2]) for target in TARGETS
                ],
            },
            json_dumps_params={"indent": 2},
        )
