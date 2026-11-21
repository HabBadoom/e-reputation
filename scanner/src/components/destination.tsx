"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner } from "@/components/ui";
import { api, type AnyPulseLink, type BrandRef, type CollectionSession } from "@/lib/api";

// Choose where the open collection will be sent: an AnyPulse organization the account is linked
// to, and one of its brands (listed live from AnyPulse through the signed integration).
export function DestinationPicker({ session }: { session: CollectionSession }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const d = session.destination;
  const [linkId, setLinkId] = useState(d?.link_active ? d.link_id : "");
  const [brandId, setBrandId] = useState(d?.link_active ? (d.brand_id ?? "") : "");
  useEffect(() => {
    setLinkId(d?.link_active ? d.link_id : "");
    setBrandId(d?.link_active ? (d.brand_id ?? "") : "");
  }, [d?.link_id, d?.brand_id, d?.link_active]);

  const links = useQuery({ queryKey: ["links"], queryFn: () => api.get<AnyPulseLink[]>("/links") });
  const active = (links.data ?? []).filter((l) => l.status === "active");
  const brands = useQuery({
    queryKey: ["brands", linkId],
    queryFn: () => api.get<BrandRef[]>(`/links/${linkId}/brands`),
    enabled: !!linkId,
    retry: false,
  });
  const save = useMutation({
    mutationFn: () =>
      api.patch<CollectionSession>(`/sessions/${session.id}`, { link_id: linkId || null, brand_id: brandId || null }),
    onSuccess: (s) => qc.setQueryData(["session", "current"], s),
  });
  const dirty = linkId !== (d?.link_active ? d.link_id : "") || brandId !== (d?.link_active ? (d.brand_id ?? "") : "");

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">{t("dest.title")}</h3>
      {d && d.link_active && d.brand_name && (
        <p className="text-sm">{t("dest.current", { org: d.organization_name, brand: d.brand_name })}</p>
      )}
      {d && !d.link_active && <p className="text-sm text-signal-warn">{t("dest.revoked")}</p>}
      {!d && <p className="text-sm text-ink-mute">{t("dest.none")}</p>}

      {links.data && active.length === 0 ? (
        <p className="text-sm text-ink-soft">
          {t("dest.noLinks")}{" "}
          <Link className="text-brand hover:underline" href="/links">{t("dest.goLinks")}</Link>
        </p>
      ) : (
        session.is_open && (
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <label className="block">
              <span className="label">{t("dest.org")}</span>
              <select className="field" value={linkId}
                      onChange={(e) => { setLinkId(e.target.value); setBrandId(""); }}>
                <option value="">{t("dest.choose")}</option>
                {active.map((l) => <option key={l.id} value={l.id}>{l.organization_name}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label">{t("dest.brand")}</span>
              <select className="field" value={brandId} disabled={!linkId || brands.isLoading}
                      onChange={(e) => setBrandId(e.target.value)}>
                <option value="">{t("dest.choose")}</option>
                {(brands.data ?? []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </label>
            <Button disabled={!dirty || save.isPending || (!!linkId && !brandId)} onClick={() => save.mutate()}>
              {t("dest.save")}
            </Button>
          </div>
        )
      )}
      {brands.data && brands.data.length === 0 && <p className="text-xs text-ink-mute">{t("dest.noBrands")}</p>}
      <ErrorBanner error={links.error ?? brands.error ?? save.error} />
    </div>
  );
}
