import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { WorkspaceShell } from "@/app/(workspace)/workspace-shell";
import { auth } from "@/lib/auth";

export default async function WorkspaceLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session) redirect("/sign-in");
  return <WorkspaceShell email={session.user.email}>{children}</WorkspaceShell>;
}
