import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { SessionInfo } from "../types";

const login = vi.fn();
vi.mock("../api", () => ({
  api: { login: (u: string, p: string) => login(u, p) },
  onAuthChange: () => () => {},
}));

import { LoginView } from "../views/LoginView";

const LOCKED: SessionInfo = {
  authenticated: false, operator: null, via: null, csrf_token: null,
  mode: "password", configured: false,
  reason: "operator login is not configured: set RECONS_OPERATOR_USER and RECONS_OPERATOR_PASSWORD_HASH",
};
const READY: SessionInfo = { ...LOCKED, configured: true, reason: null };

describe("LoginView", () => {
  beforeEach(() => login.mockReset());

  it("explains the locked state and does not offer a form that can succeed", () => {
    render(<LoginView info={LOCKED} onSignedIn={vi.fn()} />);
    expect(screen.getByText(/not configured/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeDisabled();
  });

  it("signs in with username and password", async () => {
    const onSignedIn = vi.fn();
    login.mockResolvedValue({ ...READY, authenticated: true, operator: "tony", csrf_token: "t" });
    render(<LoginView info={READY} onSignedIn={onSignedIn} />);
    fireEvent.change(screen.getByLabelText(/username/i), { target: { value: "tony" } });
    const pw = screen.getByLabelText(/password/i) as HTMLInputElement;
    expect(pw.type).toBe("password");
    expect(pw.autocomplete).toBe("current-password");
    fireEvent.change(pw, { target: { value: "hunter2hunter2" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("tony", "hunter2hunter2"));
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledWith(expect.objectContaining({ operator: "tony" })));
  });

  it("shows a friendly error for bad credentials and rate limiting", async () => {
    login.mockRejectedValueOnce(new Error("invalid credentials"));
    render(<LoginView info={READY} onSignedIn={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/username/i), { target: { value: "tony" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText(/wrong username or password/i)).toBeInTheDocument();

    login.mockRejectedValueOnce(new Error("too many login attempts"));
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText(/too many attempts/i)).toBeInTheDocument();
  });

  it("in proxy mode tells the operator sign-in happens upstream", () => {
    render(
      <LoginView
        info={{ ...READY, mode: "proxy", reason: null }}
        onSignedIn={vi.fn()}
      />,
    );
    expect(screen.getByText(/access proxy/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });
});
