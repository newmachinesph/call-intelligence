"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { NotificationBell } from "@/app/(workspace)/notification-bell";
import { PageTour } from "@/app/(workspace)/page-tour";
import { SignOutButton } from "@/app/sign-out-button";

// Call-intelligence destinations only; reference modules without data (Financials, Collections…) stay hidden.
const navigation = [
  { href: "/", match: "/", label: "Home", icon: "home" },
  { href: "/tasks", match: "/tasks", label: "Tasks", icon: "tasks" },
  { href: "/team", match: "/team", label: "Team", icon: "team" },
  { href: "/assistant", match: "/assistant", label: "Assistant", icon: "assistant" },
  { href: "/sales", match: "/sales", label: "Sales", icon: "sales" },
] as const;

function NavIcon({ name }: { name: (typeof navigation)[number]["icon"] }) {
  const paths: Record<string, React.ReactNode> = {
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" /><path d="M9 21v-7h6v7" /></>,
    tasks: <><path d="M9 6h12M9 12h12M9 18h12" /><path d="m3 6 1 1 2-2M3 12l1 1 2-2M3 18l1 1 2-2" /></>,
    team: <><circle cx="9" cy="8" r="3" /><path d="M3 20v-2a5 5 0 0 1 10 0v2M16 5a3 3 0 0 1 0 6M17 14a5 5 0 0 1 4 5v1" /></>,
    assistant: <><path d="M8 14a6 6 0 1 1 8 0l-1.5 2H9.5zM9 19h6M10 22h4" /></>,
    sales: <><path d="M4 20V9M10 20V4M16 20v-7M22 20H2" /></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

export function WorkspaceShell({ children, email }: Readonly<{ children: React.ReactNode; email: string }>) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    // Mobile drawer: focus moves into it, Escape closes it and returns focus to the Menu button.
    sidebar.current?.querySelector<HTMLElement>("a[href], input:not([type=hidden]), button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      menuButton.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);
  return (
    <div className="workspace-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <button ref={menuButton} className="mobile-menu-button" type="button" aria-expanded={menuOpen} aria-controls="workspace-navigation" onClick={() => setMenuOpen(!menuOpen)}>
        <span aria-hidden="true">☰</span> Menu
      </button>
      {menuOpen && <button className="nav-scrim" type="button" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
      <aside ref={sidebar} className={`sidebar ${menuOpen ? "sidebar-open" : ""}`} id="workspace-navigation" aria-label="Workspace">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">P</span>
          <span><strong>Call Intelligence</strong><small>Sales call review</small></span>
        </div>
        <form className="sidebar-search" action="/sales" method="get" role="search" onSubmit={() => setMenuOpen(false)}>
          <input type="hidden" name="view" value="calls" />
          <label className="sr-only" htmlFor="sidebar-search">Search calls</label>
          <input id="sidebar-search" name="q" type="search" placeholder="Search calls" maxLength={100} />
        </form>
        <div className="sidebar-divider" />
        <nav className="primary-nav" aria-label="Main navigation">
          {navigation.map((item) => {
            const selected = item.match === "/" ? pathname === "/" : pathname.startsWith(item.match);
            return <Link key={item.href} href={item.href} className={`nav-link ${selected ? "nav-link-selected" : ""}`} aria-current={selected ? "page" : undefined} onClick={() => setMenuOpen(false)}><NavIcon name={item.icon} /><span>{item.label}</span></Link>;
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-account">
            <span className="account-label">Signed in</span>
            <div className="account-row"><strong>{email}</strong><SignOutButton /></div>
          </div>
        </div>
      </aside>
      <main className="canvas" id="main-content" tabIndex={-1}>
        <div className="canvas-actions"><PageTour /><NotificationBell /></div>
        {children}
      </main>
    </div>
  );
}
