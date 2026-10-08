"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AuthCard } from "@/components/AuthCard";
import { Alert, Button } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function Invite() {
  const token = useSearchParams().get("token") ?? "";
  const { user, reload, setOrgId } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <AuthCard title="Team invitation" footer={<Link href="/dashboard">Dashboard</Link>}>
      {!user ? (
        <div className="space-y-3 text-sm">
          <p>Sign in with the invited email address, or create an account, to accept this invitation.</p>
          <div className="flex gap-2">
            <Link href={`/register?invite=${encodeURIComponent(token)}`}>
              <Button>Create account</Button>
            </Link>
            <Link href={`/login?next=${encodeURIComponent(`/invite?token=${token}`)}`}>
              <Button variant="secondary">Sign in</Button>
            </Link>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm">Signed in as {user.email}.</p>
          {error && <Alert>{error}</Alert>}
          <Button
            loading={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const { data } = await api.POST("/api/v1/auth/invitations/accept", { body: { token } });
                await reload();
                if (data?.org_id) setOrgId(data.org_id);
                router.push("/team");
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Accept invitation
          </Button>
        </div>
      )}
    </AuthCard>
  );
}

export default function InvitePage() {
  return (
    <Suspense>
      <Invite />
    </Suspense>
  );
}
