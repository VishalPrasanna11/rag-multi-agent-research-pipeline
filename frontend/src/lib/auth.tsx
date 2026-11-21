import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { setTokenGetter } from "./api";
import { useMeta } from "./hooks";

const ClerkBridge = lazy(async () => {
  const clerk = await import("@clerk/react");
  function Bridge({ pk, children }: { pk: string; children: ReactNode }) {
    return (
      <clerk.ClerkProvider publishableKey={pk}>
        <TokenSync useAuth={clerk.useAuth} />
        <clerk.Show when="signed-out"><SignInGate SignIn={clerk.SignIn} /></clerk.Show>
        <clerk.Show when="signed-in">{children}</clerk.Show>
      </clerk.ClerkProvider>
    );
  }
  return { default: Bridge };
});

function TokenSync({ useAuth }: { useAuth: () => { getToken: () => Promise<string | null> } }) {
  const { getToken } = useAuth();
  useEffect(() => { setTokenGetter(() => getToken()); return () => setTokenGetter(null); }, [getToken]);
  return null;
}

function SignInGate({ SignIn }: { SignIn: React.ComponentType<any> }) {
  return <main className="grid min-h-screen place-items-center p-4"><SignIn routing="hash" /></main>;
}

/** Clerk when the server reports a publishable key; otherwise a signed anonymous cookie (per-browser data). */
export function AuthProvider({ children }: { children: ReactNode }) {
  const { data: meta, isLoading } = useMeta();
  if (isLoading) return null;
  const pk = meta?.auth.clerk_publishable_key;
  if (pk) return <Suspense fallback={null}><ClerkBridge pk={pk}>{children}</ClerkBridge></Suspense>;
  return <>{children}</>;
}
