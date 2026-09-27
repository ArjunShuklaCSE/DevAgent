"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname } from "next/navigation";

import { api, loginUrl } from "@/lib/api";

/** Sign-in state in the nav. Hidden when the server has no GitHub OAuth app configured. */
export function Account() {
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000 });
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => queryClient.invalidateQueries(),
  });
  if (!me.data?.oauth_enabled) return null;
  const user = me.data.user;
  if (!user) {
    return (
      <a
        href={loginUrl(pathname)}
        className="rounded-md bg-zinc-900 px-3 py-1 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
      >
        Sign in with GitHub
      </a>
    );
  }
  return (
    <div className="flex items-center gap-2 text-sm">
      {user.avatar_url && (
        // eslint-disable-next-line @next/next/no-img-element -- GitHub avatar, no optimization needed
        <img src={user.avatar_url} alt="" className="size-6 rounded-full" />
      )}
      <span className="text-zinc-600 dark:text-zinc-300">{user.login}</span>
      <button
        type="button"
        onClick={() => logout.mutate()}
        className="rounded px-1.5 py-0.5 text-xs text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
      >
        Sign out
      </button>
    </div>
  );
}
