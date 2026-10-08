/**
 * Live-API integration test for the bearer-token client used by the mobile app.
 * Runs only when MIND_API_URL is set (e.g. MIND_API_URL=http://localhost:8000).
 */
import { describe, expect, it } from "vitest";
import { ApiError, MindClient, memoryTokenStore } from "./index";

const base = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.MIND_API_URL;

describe.skipIf(!base)("mobile-style client against a running API", () => {
  it("registers, refreshes tokens transparently and reads shared projects", async () => {
    const store = memoryTokenStore();
    const owner = new MindClient({ baseUrl: base!, auth: "bearer", tokenStore: store });
    const email = `mobile-${Date.now()}@example.com`;
    await owner.register({ email, password: "mobile horse battery", display_name: "Mobile" });
    const { data: orgs } = await owner.api.GET("/api/v1/orgs");
    const team = (await owner.api.POST("/api/v1/orgs", { body: { name: "Mobile team" } })).data!;
    await owner.api.POST("/api/v1/projects", { body: { org_id: team.id, name: "Shared robot" } });
    expect(orgs!.length).toBe(1);

    // Invite a second user, who signs in from "a phone" and sees the shared project.
    const inv = (await owner.api.POST("/api/v1/orgs/{org_id}/invitations", { params: { path: { org_id: team.id } }, body: { email: `m2-${email}`, role: "viewer" } })).data!;
    const phone = new MindClient({ baseUrl: base!, auth: "bearer", tokenStore: memoryTokenStore() });
    await phone.register({ email: `m2-${email}`, password: "mobile horse battery", display_name: "Phone", invitation_token: inv.token });
    const { data: projects } = await phone.api.GET("/api/v1/projects", { params: { query: { org_id: team.id } } });
    expect(projects!.map((p) => p.name)).toEqual(["Shared robot"]);
    expect(projects![0].role).toBe("viewer");

    // Viewer permissions are enforced by the server, not the client.
    await expect(phone.api.POST("/api/v1/projects", { body: { org_id: team.id, name: "nope" } })).rejects.toBeInstanceOf(ApiError);

    // An invalid access token triggers one transparent refresh using the stored refresh token.
    const t = await store.get();
    await store.set({ access: "expired.invalid.token", refresh: t!.refresh });
    const { data: me } = await owner.api.GET("/api/v1/auth/me");
    expect(me!.email).toBe(email);
    expect((await store.get())!.access).not.toBe("expired.invalid.token");
  });
});
