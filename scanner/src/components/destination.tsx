"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner } from "@/components/ui";
import { api, type BrandRef, type CollectionSession } from "@/lib/api";

// The brand this collection is about, in the account's own AnyPulse organization. Brands are
// listed live from AnyPulse (signed integration) and can be created here.
export function DestinationPicker({ session }: { session: CollectionSession }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const d = session.destination;
  const [brandId, setBrandId] = useState(d?.access_active ? (d.brand_id ?? "") : "");
  const [newName, setNewName] = useState("");
  useEffect(() => setBrandId(d?.access_active ? (d.brand_id ?? "") : ""), [d?.brand_id, d?.access_active]);

  const brands = useQuery({
    queryKey: ["brands"],
    queryFn: () => api.get<BrandRef[]>("/brands"),
    enabled: session.is_open,
    retry: false,
  });
  const save = useMutation({
    mutationFn: (id: string) => api.patch<CollectionSession>(`/sessions/${session.id}`, { brand_id: id || null }),
    onSuccess: (s) => qc.setQueryData(["session", "current"], s),
  });
  const create = useMutation({
    mutationFn: () => api.post<BrandRef>("/brands", { name: newName }),
    onSuccess: (b) => {
      setNewName("");
      setBrandId(b.id);
      qc.invalidateQueries({ queryKey: ["brands"] });
      save.mutate(b.id);
    },
  });
  const dirty = brandId !== (d?.access_active ? (d.brand_id ?? "") : "");

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">{t("dest.title")}</h3>
        <p className="text-xs text-ink-mute">{t("dest.help")}</p>
      </div>
      {d && d.access_active && d.brand_name && (
        <p className="text-sm">{t("dest.current", { org: d.organization_name, brand: d.brand_name })}</p>
      )}
      {d && !d.access_active && <p className="text-sm text-signal-warn">{t("dest.revoked")}</p>}
      {!d && <p className="text-sm text-ink-mute">{t("dest.none")}</p>}

      {session.is_open && brands.data && (
        <>
          {brands.data.length === 0 ? (
            <p className="text-sm text-ink-soft">{t("dest.noBrands")}</p>
          ) : (
            <div className="flex flex-wrap items-end gap-2">
              <select className="field max-w-xs" value={brandId} aria-label={t("dest.title")}
                      onChange={(e) => setBrandId(e.target.value)}>
                <option value="">{t("dest.choose")}</option>
                {brands.data.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
              <Button disabled={!dirty || save.isPending} onClick={() => save.mutate(brandId)}>
                {t("dest.save")}
              </Button>
            </div>
          )}
          <form className="flex flex-wrap items-end gap-2"
                onSubmit={(e) => { e.preventDefault(); if (newName.trim()) create.mutate(); }}>
            <input className="field max-w-xs" placeholder={t("dest.newBrand")} aria-label={t("dest.newBrand")}
                   maxLength={200} value={newName} onChange={(e) => setNewName(e.target.value)} />
            <Button type="submit" variant="ghost" disabled={!newName.trim() || create.isPending}>
              {t("dest.create")}
            </Button>
          </form>
        </>
      )}
      <ErrorBanner error={brands.error ?? save.error ?? create.error} />
    </div>
  );
}
