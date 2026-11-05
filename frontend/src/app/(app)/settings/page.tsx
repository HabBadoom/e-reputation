"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, Card, ErrorBanner, Field, Spinner } from "@/components/ui";
import { api, type Organization } from "@/lib/api";
import { LOCALES } from "@/lib/i18n";
import { useCan } from "@/lib/session";

export default function SettingsPage() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canEdit = useCan("tenant:update");
  const org = useQuery({ queryKey: ["org"], queryFn: () => api.get<Organization>("/organization") });
  const [f, setF] = useState({ name: "", default_locale: "fr" });
  useEffect(() => { if (org.data) setF({ name: org.data.name, default_locale: org.data.default_locale }); }, [org.data]);
  const save = useMutation({
    mutationFn: () => api.patch<Organization>("/organization", f),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["org"] }); qc.invalidateQueries({ queryKey: ["me"] }); },
  });
  if (org.isLoading) return <Spinner />;
  return (
    <div className="max-w-xl space-y-6">
      <h1 className="text-2xl font-semibold">{t("settings.title")}</h1>
      <Card>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <ErrorBanner error={org.error ?? save.error} />
          <fieldset disabled={!canEdit} className="space-y-4">
            <Field label={t("auth.orgName")} required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
            <label className="block"><span className="label">{t("settings.language")}</span>
              <select className="field" value={f.default_locale} onChange={(e) => setF({ ...f, default_locale: e.target.value })}>
                {LOCALES.map((l) => <option key={l} value={l}>{t(`lang.${l}`)}</option>)}
              </select>
            </label>
          </fieldset>
          {canEdit && (
            <div className="flex items-center gap-3">
              <Button type="submit" disabled={save.isPending}>{t("common.save")}</Button>
              {save.isSuccess && <span className="text-sm text-signal-pos">{t("common.saved")}</span>}
            </div>
          )}
          <p className="font-mono text-xs text-ink-mute" dir="ltr">{org.data?.slug}</p>
        </form>
      </Card>
    </div>
  );
}
