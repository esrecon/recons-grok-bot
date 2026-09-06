import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// The real client: cookies ride along same-origin, the CSRF token from the
// session is sent on every mutation, and a 401 anywhere flips the app to
// signed-out instead of failing silently.
import { api, onAuthChange, resetAuthForTests } from "../api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("api client auth plumbing", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    resetAuthForTests();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads the session and sends the CSRF token on mutations only", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ authenticated: true, operator: "tony", via: "password", csrf_token: "tok-123", mode: "password", configured: true }),
    );
    const s = await api.session();
    expect(s.authenticated).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/session");
    expect(init.credentials).toBe("same-origin");

    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.listAgents();
    const getHeaders = new Headers(fetchMock.mock.calls[1][1].headers);
    expect(getHeaders.get("x-csrf-token")).toBeNull();

    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "x" }, 201));
    await api.createAgent({ name: "X", role: "y", tier: "workhorse", avatar_color: "#000000" });
    const postHeaders = new Headers(fetchMock.mock.calls[2][1].headers);
    expect(postHeaders.get("x-csrf-token")).toBe("tok-123");
    expect(postHeaders.get("content-type")).toBe("application/json");
  });

  it("login posts JSON and stores the token; logout clears it", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ authenticated: true, operator: "tony", via: "password", csrf_token: "tok-9", mode: "password", configured: true }),
    );
    await api.login("tony", "pw");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ username: "tony", password: "pw" });
    // Login itself carries no CSRF token (there is no session yet).
    expect(new Headers(init.headers).get("x-csrf-token")).toBeNull();

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await api.logout();
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get("x-csrf-token")).toBe("tok-9");

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await api.deleteAgent("x");
    expect(new Headers(fetchMock.mock.calls[2][1].headers).get("x-csrf-token")).toBeNull();
  });

  it("a 401 notifies auth listeners and rejects", async () => {
    const listener = vi.fn();
    onAuthChange(listener);
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "authentication required" }, 401));
    await expect(api.listAgents()).rejects.toThrow(/sign/i);
    expect(listener).toHaveBeenCalledWith("signed-out");
  });

  it("surfaces the server's detail for other errors", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "agent is paused — resume it to chat" }, 409));
    await expect(api.setStatus("x", "pause")).rejects.toThrow(/paused/);
  });

  it("credential writes send only the key and value and expect no value back", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ authenticated: true, csrf_token: "t", mode: "password", configured: true }),
    );
    await api.session();
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ key: "NOUS_API_KEY", action: "created", configured: true, restart_required: true }),
    );
    const r = await api.setCredential("NOUS_API_KEY", "value-1");
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/settings/credentials/NOUS_API_KEY");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ value: "value-1" });
    expect(r.action).toBe("created");
    expect(JSON.stringify(r)).not.toContain("value-1");
  });
});
