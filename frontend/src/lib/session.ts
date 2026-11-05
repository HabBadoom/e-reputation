"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type Me } from "./api";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/auth/me"), staleTime: 60_000 });
}

export function useCan(perm: string): boolean {
  const { data } = useMe();
  return !!data?.permissions.includes(perm);
}
