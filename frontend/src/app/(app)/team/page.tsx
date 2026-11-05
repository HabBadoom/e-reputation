"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Field, Spinner } from "@/components/ui";
import { api, type Invitation, type Member, type RoleKey } from "@/lib/api";
import { useCan, useMe } from "@/lib/session";

const RANK: Record<RoleKey, number> = { owner: 100, admin: 80, analyst: 50, viewer: 10 };
const ROLES: RoleKey[] = ["owner", "admin", "analyst", "viewer"];

export default function TeamPage() {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const router = useRouter();
  const me = useMe().data!;
  const canManage = useCan("members:manage");
  const myRank = RANK[me.tenant.role];
  const assignable = ROLES.filter((r) => RANK[r] <= myRank && (r !== "owner" || me.tenant.role === "owner"));

  const members = useQuery({ queryKey: ["members"], queryFn: () => api.get<Member[]>("/members") });
  const invites = useQuery({ queryKey: ["invitations"], queryFn: () => api.get<Invitation[]>("/invitations"), enabled: canManage });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<RoleKey>("viewer");
  const [link, setLink] = useState<string | null>(null);

  const invite = useMutation({
    mutationFn: () => api.post<Invitation>("/invitations", { email, role }),
    onSuccess: (inv) => {
      setLink(`${window.location.origin}/invite?token=${encodeURIComponent(inv.token ?? "")}`);
      setEmail("");
      qc.invalidateQueries({ queryKey: ["invitations"] });
    },
  });
  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: RoleKey }) => api.patch(`/members/${id}`, { role }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }),
  });
  const remove = useMutation({
    mutationFn: (m: Member) => api.del(`/members/${m.membership_id}`),
    onSuccess: (_d, m) => {
      if (m.user_id === me.user_id) { qc.clear(); router.replace("/login"); }
      else qc.invalidateQueries({ queryKey: ["members"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.del(`/invitations/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invitations"] }),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{t("team.title")}</h1>
      <ErrorBanner error={changeRole.error ?? remove.error} />
      <Card>
        {members.isLoading ? <Spinner /> : (
          <ul className="-my-2 divide-y divide-line">
            {members.data?.map((m) => {
              const self = m.user_id === me.user_id;
              const manageable = canManage && !self && (RANK[m.role] < myRank || me.tenant.role === "owner");
              return (
                <li key={m.membership_id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div>
                    <p className="text-sm font-medium">{m.full_name} {self && <Badge>{t("team.you")}</Badge>}</p>
                    <p className="text-xs text-ink-mute" dir="ltr">{m.email}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {manageable ? (
                      <select className="field w-auto" value={m.role}
                              onChange={(e) => changeRole.mutate({ id: m.membership_id, role: e.target.value as RoleKey })}>
                        {ROLES.filter((r) => assignable.includes(r) || r === m.role).map((r) =>
                          <option key={r} value={r}>{t(`role.${r}`)}</option>)}
                      </select>
                    ) : <Badge tone={m.role === "owner" ? "brand" : "neutral"}>{t(`role.${m.role}`)}</Badge>}
                    {(manageable || self) && (
                      <Button variant="danger" onClick={() => confirm(t("common.confirmDelete")) && remove.mutate(m)}>
                        {self ? t("team.leave") : t("team.remove")}
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {canManage && (
        <Card title={t("team.invite")}>
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); setLink(null); invite.mutate(); }}>
            <div className="min-w-56 flex-1">
              <Field label={t("auth.email")} type="email" dir="ltr" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <label><span className="label">{t("team.role")}</span>
              <select className="field" value={role} onChange={(e) => setRole(e.target.value as RoleKey)}>
                {assignable.map((r) => <option key={r} value={r}>{t(`role.${r}`)}</option>)}
              </select>
            </label>
            <Button type="submit" disabled={invite.isPending}>{t("team.invite")}</Button>
          </form>
          <div className="mt-3"><ErrorBanner error={invite.error} /></div>
          {link && (
            <div className="mt-4 rounded border border-brand/30 bg-brand-tint p-3">
              <p className="mb-2 text-xs text-brand-strong">{t("team.inviteLink")}</p>
              <input readOnly className="field font-mono text-xs" dir="ltr" value={link} onFocus={(e) => e.target.select()} />
            </div>
          )}
          {!!invites.data?.length && (
            <>
              <h3 className="mb-2 mt-6 text-xs font-medium uppercase tracking-wide text-ink-mute">{t("team.pending")}</h3>
              <ul className="divide-y divide-line text-sm">
                {invites.data.map((i) => (
                  <li key={i.id} className="flex items-center justify-between gap-3 py-2">
                    <span dir="ltr">{i.email}</span>
                    <span className="flex items-center gap-3 text-ink-mute">
                      <Badge>{t(`role.${i.role}`)}</Badge>
                      {t("team.expires")} {fmtDate(i.expires_at)}
                      <button className="text-signal-neg hover:underline" onClick={() => revoke.mutate(i.id)}>{t("common.delete")}</button>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      )}
    </div>
  );
}
