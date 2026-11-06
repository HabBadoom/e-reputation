"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Field, fieldErrors, Spinner } from "@/components/ui";
import { api, type MfaSetup, type MfaStatus, type RecoveryCodes } from "@/lib/api";
import { useMe } from "@/lib/session";

function RecoveryCodeList({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded border border-signal-warn/40 bg-signal-warn/5 p-4">
      <h3 className="mb-1 text-sm font-semibold">{t("mfa.codesTitle")}</h3>
      <p className="mb-3 text-xs text-ink-soft">{t("mfa.codesHelp")}</p>
      <ul className="mb-4 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-sm" dir="ltr">
        {codes.map((c) => <li key={c}>{c}</li>)}
      </ul>
      <div className="flex gap-2">
        <Button variant="ghost" onClick={async () => {
          await navigator.clipboard.writeText(codes.join("\n"));
          setCopied(true);
        }}>{copied ? t("mfa.copied") : t("mfa.copy")}</Button>
        <Button onClick={onDone}>{t("mfa.done")}</Button>
      </div>
    </div>
  );
}

function MfaSection() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["mfa"], queryFn: () => api.get<MfaStatus>("/auth/mfa") });
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [mode, setMode] = useState<"idle" | "disable" | "regen">("idle");

  useEffect(() => {
    if (!setup) return setQr(null);
    // Rendered locally: the secret never leaves the browser to a third-party QR service.
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 192 }).then(setQr).catch(() => setQr(null));
  }, [setup]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["mfa"] });
    qc.invalidateQueries({ queryKey: ["me"] });
  };
  const start = useMutation({ mutationFn: () => api.post<MfaSetup>("/auth/mfa/setup"), onSuccess: setSetup });
  const enable = useMutation({
    mutationFn: () => api.post<RecoveryCodes>("/auth/mfa/enable", { code }),
    onSuccess: (r) => { setCodes(r.recovery_codes); setSetup(null); setCode(""); refresh(); },
  });
  const disable = useMutation({
    mutationFn: () => api.post("/auth/mfa/disable", { password, code }),
    onSuccess: () => { setMode("idle"); setCode(""); setPassword(""); refresh(); },
  });
  const regen = useMutation({
    mutationFn: () => api.post<RecoveryCodes>("/auth/mfa/recovery-codes", { code }),
    onSuccess: (r) => { setCodes(r.recovery_codes); setMode("idle"); setCode(""); refresh(); },
  });

  if (status.isLoading) return <Spinner />;
  const s = status.data!;

  return (
    <Card title={<span className="flex items-center gap-2">{t("mfa.title")}
      {s.enabled && <Badge tone="brand">{t("mfa.on")}</Badge>}</span>}>
      <div className="space-y-4">
        {codes && <RecoveryCodeList codes={codes} onDone={() => setCodes(null)} />}

        {!s.enabled && !setup && (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-ink-soft">{t("mfa.off")}</p>
            <Button onClick={() => start.mutate()} disabled={start.isPending}>{t("mfa.enable")}</Button>
          </div>
        )}
        <ErrorBanner error={start.error} />

        {setup && (
          <div className="grid gap-5 md:grid-cols-[auto_1fr]">
            <div className="flex h-48 w-48 items-center justify-center rounded border border-line bg-white">
              {qr ? <img src={qr} alt="QR code" width={192} height={192} /> : <Spinner />}
            </div>
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); enable.mutate(); }}>
              <p className="text-sm text-ink-soft">{t("mfa.scan")}</p>
              <p className="text-xs text-ink-mute">{t("mfa.manual")}</p>
              <code className="block break-all rounded bg-paper-sunk px-2 py-1 font-mono text-sm" dir="ltr">
                {setup.secret.match(/.{1,4}/g)?.join(" ")}
              </code>
              <ErrorBanner error={enable.error} />
              <Field label={t("auth.mfaCode")} required inputMode="numeric" autoComplete="one-time-code"
                     dir="ltr" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} />
              <div className="flex gap-2">
                <Button type="submit" disabled={enable.isPending || code.length !== 6}>{t("mfa.confirm")}</Button>
                <Button type="button" variant="ghost" onClick={() => { setSetup(null); setCode(""); }}>
                  {t("common.cancel")}
                </Button>
              </div>
            </form>
          </div>
        )}

        {s.enabled && mode === "idle" && (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-ink-soft">{t("mfa.codesRemaining", { n: s.recovery_codes_remaining })}</p>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setMode("regen")}>{t("mfa.regenerate")}</Button>
              <Button variant="danger" onClick={() => setMode("disable")}>{t("mfa.disable")}</Button>
            </div>
          </div>
        )}

        {s.enabled && mode !== "idle" && (
          <form className="space-y-3" onSubmit={(e) => {
            e.preventDefault();
            if (mode === "disable") disable.mutate(); else regen.mutate();
          }}>
            <ErrorBanner error={mode === "disable" ? disable.error : regen.error} />
            {mode === "disable" && (
              <Field label={t("account.currentPassword")} type="password" required
                     autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            )}
            <Field label={t("mfa.codePrompt")} required dir="ltr" autoComplete="one-time-code" maxLength={20}
                   value={code} onChange={(e) => setCode(e.target.value)} />
            <div className="flex gap-2">
              <Button type="submit" variant={mode === "disable" ? "danger" : "primary"}
                      disabled={disable.isPending || regen.isPending}>
                {mode === "disable" ? t("mfa.disable") : t("mfa.regenerate")}
              </Button>
              <Button type="button" variant="ghost" onClick={() => { setMode("idle"); setCode(""); setPassword(""); }}>
                {t("common.cancel")}
              </Button>
            </div>
          </form>
        )}
      </div>
    </Card>
  );
}

function PasswordSection() {
  const { t } = useI18n();
  const [f, setF] = useState({ current_password: "", new_password: "" });
  const m = useMutation({
    mutationFn: () => api.post("/auth/password/change", f),
    onSuccess: () => setF({ current_password: "", new_password: "" }),
  });
  const fe = fieldErrors(m.error);
  return (
    <Card title={t("account.password")}>
      <form className="max-w-sm space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        {!fe.new_password && <ErrorBanner error={m.error} />}
        <Field label={t("account.currentPassword")} type="password" required autoComplete="current-password"
               value={f.current_password} onChange={(e) => setF({ ...f, current_password: e.target.value })} />
        <Field label={t("auth.newPassword")} type="password" required minLength={12} autoComplete="new-password"
               hint={t("auth.passwordHint")} error={fe.new_password} value={f.new_password}
               onChange={(e) => setF({ ...f, new_password: e.target.value })} />
        <Button type="submit" disabled={m.isPending}>{t("account.changePassword")}</Button>
        {m.isSuccess && <p role="status" className="text-sm text-signal-pos">{t("account.passwordChanged")}</p>}
      </form>
    </Card>
  );
}

export default function AccountPage() {
  const { t } = useI18n();
  const me = useMe().data!;
  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">{t("account.title")}</h1>
      <Card title={t("account.email")}>
        <p className="flex items-center gap-3 text-sm">
          <span dir="ltr">{me.email}</span>
          <Badge tone={me.email_verified ? "brand" : "warn"}>
            {me.email_verified ? t("account.verified") : t("account.unverified")}
          </Badge>
        </p>
      </Card>
      <PasswordSection />
      <MfaSection />
    </div>
  );
}
