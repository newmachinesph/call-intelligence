import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { SignInForm } from "@/app/sign-in/sign-in-form";
import { auth } from "@/lib/auth";

export default async function SignIn() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (session) redirect("/");
  return <SignInForm />;
}
