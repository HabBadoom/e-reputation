"""Email templates in French, Arabic and English. Plain, inline-styled HTML that renders in
every mail client, plus a text part. User-supplied values are HTML-escaped."""

from html import escape

from app.core.config import get_settings
from app.modules.notifications.email import EmailMessage

_T = {
    "invite": {
        "fr": ("{inviter} vous invite sur {app}",
               "Bonjour,",
               "{inviter} vous invite à rejoindre l'organisation « {org} » sur {app}, la plateforme de veille de réputation.",
               "Rejoindre {org}",
               "Ce lien expire dans 7 jours. Utilisez cette adresse e-mail pour créer votre compte ou vous connecter."),
        "ar": ("{inviter} يدعوك إلى {app}",
               "مرحبًا،",
               "يدعوك {inviter} للانضمام إلى مؤسسة «{org}» على {app}، منصة رصد السمعة.",
               "الانضمام إلى {org}",
               "تنتهي صلاحية هذا الرابط خلال 7 أيام. استخدم هذا البريد الإلكتروني لإنشاء حسابك أو تسجيل الدخول."),
        "en": ("{inviter} invited you to {app}",
               "Hello,",
               "{inviter} invited you to join the organization “{org}” on {app}, the reputation monitoring platform.",
               "Join {org}",
               "This link expires in 7 days. Use this email address to create your account or sign in."),
    },
    "verify": {
        "fr": ("Confirmez votre adresse e-mail",
               "Bonjour {name},",
               "Confirmez votre adresse e-mail pour sécuriser votre compte {app}.",
               "Confirmer mon adresse",
               "Ce lien expire dans 48 heures. Si vous n'avez pas créé de compte, ignorez ce message."),
        "ar": ("تأكيد بريدك الإلكتروني",
               "مرحبًا {name}،",
               "يُرجى تأكيد بريدك الإلكتروني لتأمين حسابك على {app}.",
               "تأكيد البريد الإلكتروني",
               "تنتهي صلاحية هذا الرابط خلال 48 ساعة. إذا لم تنشئ حسابًا، تجاهل هذه الرسالة."),
        "en": ("Confirm your email address",
               "Hello {name},",
               "Please confirm your email address to secure your {app} account.",
               "Confirm my email",
               "This link expires in 48 hours. If you didn't create an account, ignore this email."),
    },
    "reset": {
        "fr": ("Réinitialisation de votre mot de passe",
               "Bonjour {name},",
               "Une réinitialisation du mot de passe de votre compte {app} a été demandée.",
               "Choisir un nouveau mot de passe",
               "Ce lien expire dans 1 heure et ne fonctionne qu'une fois. Si vous n'êtes pas à l'origine de cette demande, ignorez ce message : votre mot de passe reste inchangé."),
        "ar": ("إعادة تعيين كلمة المرور",
               "مرحبًا {name}،",
               "تم طلب إعادة تعيين كلمة مرور حسابك على {app}.",
               "اختيار كلمة مرور جديدة",
               "تنتهي صلاحية هذا الرابط خلال ساعة واحدة ويعمل مرة واحدة فقط. إذا لم تطلب ذلك، تجاهل هذه الرسالة وستبقى كلمة مرورك كما هي."),
        "en": ("Reset your password",
               "Hello {name},",
               "Someone asked to reset the password of your {app} account.",
               "Choose a new password",
               "This link expires in 1 hour and works once. If you didn't ask for this, ignore this email: your password stays the same."),
    },
}


def _render(kind: str, locale: str, to_email: str, name: str, link: str,
            **fmt: str) -> EmailMessage:
    s = get_settings()
    lang = locale if locale in ("fr", "ar", "en") else "fr"
    subject, greet, body, cta, foot = _T[kind][lang]
    app = s.email_from_name
    subject = subject.format(app=app, **fmt)
    greet = greet.format(name=name)
    body = body.format(app=app, **fmt)
    cta = cta.format(app=app, **fmt)
    d = "rtl" if lang == "ar" else "ltr"
    align = "right" if lang == "ar" else "left"
    html = f"""<!doctype html><html lang="{lang}" dir="{d}"><body style="margin:0;background:#f6f5f1;font-family:Arial,Helvetica,sans-serif;color:#0f1f1c">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" style="max-width:520px;background:#ffffff;border:1px solid #dcdad2;border-radius:6px" cellpadding="0" cellspacing="0">
<tr><td style="padding:28px;text-align:{align}" dir="{d}">
<p style="margin:0 0 20px;font-weight:bold;font-size:16px">{escape(app)}</p>
<p style="margin:0 0 12px;font-size:15px">{escape(greet)}</p>
<p style="margin:0 0 24px;font-size:15px;line-height:1.5">{escape(body)}</p>
<p style="margin:0 0 24px"><a href="{escape(link, quote=True)}" style="display:inline-block;background:#0d6b5c;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:6px;font-size:15px">{escape(cta)}</a></p>
<p style="margin:0 0 8px;font-size:12px;color:#6b7d79;line-height:1.5">{escape(foot)}</p>
<p style="margin:0;font-size:12px;color:#6b7d79;word-break:break-all" dir="ltr">{escape(link)}</p>
</td></tr></table></td></tr></table></body></html>"""
    text = f"{greet}\n\n{body}\n\n{cta}: {link}\n\n{foot}\n"
    return EmailMessage(to_email=to_email, to_name=name, subject=subject, html=html, text=text,
                        tags=(kind,))


def verification_email(to_email: str, name: str, locale: str, token: str) -> EmailMessage:
    link = f"{get_settings().app_base_url.rstrip('/')}/verify-email?token={token}"
    return _render("verify", locale, to_email, name, link)


def password_reset_email(to_email: str, name: str, locale: str, token: str) -> EmailMessage:
    link = f"{get_settings().app_base_url.rstrip('/')}/reset-password?token={token}"
    return _render("reset", locale, to_email, name, link)


def invitation_email(to_email: str, inviter: str, org: str, locale: str,
                     token: str) -> EmailMessage:
    link = f"{get_settings().app_base_url.rstrip('/')}/invite?token={token}"
    return _render("invite", locale, to_email, "", link, inviter=inviter, org=org)


_DIGEST = {
    "fr": ("{n} nouvelles alertes", "Bonjour {name},", "Voir les alertes",
           "Vous recevez ce message car une règle d'alerte de votre organisation vous notifie. "
           "Les règles se modifient dans Alertes → Règles."),
    "ar": ("{n} تنبيهات جديدة", "مرحبًا {name}،", "عرض التنبيهات",
           "تتلقى هذه الرسالة لأن قاعدة تنبيه في مؤسستك تُبلغك. تُعدَّل القواعد من التنبيهات ← القواعد."),
    "en": ("{n} new alerts", "Hello {name},", "View alerts",
           "You receive this because an alert rule of your organization notifies you. "
           "Rules are edited in Alerts → Rules."),
}
_SEV_COLOR = {"CRITICAL": "#8f1d1d", "HIGH": "#b4471c", "MEDIUM": "#a06a00", "LOW": "#3d5a80",
              "INFO": "#6b7d79"}


def alert_digest_email(to_email: str, name: str, locale: str,
                       items: list[tuple[str, str, str]]) -> EmailMessage:
    """items = (severity, title, body). One email per recipient per rule evaluation."""
    s = get_settings()
    lang = locale if locale in ("fr", "ar", "en") else "fr"
    many, greet, cta, foot = _DIGEST[lang]
    subject = items[0][1] if len(items) == 1 else many.format(n=len(items))
    subject = f"[{s.email_from_name}] {subject}"
    greet = greet.format(name=name)
    link = f"{s.app_base_url.rstrip('/')}/alerts"
    d = "rtl" if lang == "ar" else "ltr"
    align = "right" if lang == "ar" else "left"
    rows = "".join(
        f'<tr><td style="padding:10px 0;border-top:1px solid #ecebe6;text-align:{align}">'
        f'<span style="display:inline-block;font-size:11px;font-weight:bold;color:#fff;'
        f'background:{_SEV_COLOR.get(sev, "#6b7d79")};padding:2px 6px;border-radius:3px">'
        f'{escape(sev)}</span> <strong style="font-size:14px">{escape(title)}</strong>'
        f'<div style="font-size:13px;line-height:1.5;color:#33433f;margin-top:4px">'
        f'{escape(body)}</div></td></tr>' for sev, title, body in items)
    html = f"""<!doctype html><html lang="{lang}" dir="{d}"><body style="margin:0;background:#f6f5f1;font-family:Arial,Helvetica,sans-serif;color:#0f1f1c">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" style="max-width:560px;background:#ffffff;border:1px solid #dcdad2;border-radius:6px" cellpadding="0" cellspacing="0">
<tr><td style="padding:28px;text-align:{align}" dir="{d}">
<p style="margin:0 0 20px;font-weight:bold;font-size:16px">{escape(s.email_from_name)}</p>
<p style="margin:0 0 12px;font-size:15px">{escape(greet)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
<p style="margin:20px 0 24px"><a href="{escape(link, quote=True)}" style="display:inline-block;background:#0d6b5c;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:6px;font-size:15px">{escape(cta)}</a></p>
<p style="margin:0;font-size:12px;color:#6b7d79;line-height:1.5">{escape(foot)}</p>
</td></tr></table></td></tr></table></body></html>"""
    text = greet + "\n\n" + "\n\n".join(f"[{sev}] {t}\n{b}" for sev, t, b in items) + \
        f"\n\n{cta}: {link}\n\n{foot}\n"
    return EmailMessage(to_email=to_email, to_name=name, subject=subject, html=html, text=text,
                        tags=("alert",))
