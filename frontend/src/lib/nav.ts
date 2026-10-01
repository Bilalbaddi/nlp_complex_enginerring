import type { Role } from "./types";

export interface NavItem {
  href: string;
  label: string;
  icon: string;
  roles?: Role[]; // omitted = every role (admin always sees everything)
}

const PLANNERS: Role[] = ["commissioner", "department_head", "analyst", "admin"];

export const NAV: NavItem[] = [
  { href: "/dashboard", label: "Overview", icon: "layout" },
  { href: "/complaints", label: "Complaints", icon: "inbox" },
  { href: "/map", label: "GIS Map", icon: "map" },
  { href: "/emerging", label: "Emerging Issues", icon: "trending" },
  { href: "/service-gaps", label: "Service Gaps", icon: "gauge" },
  { href: "/forecasts", label: "Forecasts", icon: "line" },
  { href: "/kpis", label: "KPIs", icon: "chart" },
  { href: "/assistant", label: "AI Assistant", icon: "sparkles" },
  { href: "/documents", label: "Policy Documents", icon: "file" },
  { href: "/reports", label: "Reports", icon: "download", roles: PLANNERS },
  { href: "/ingestion", label: "Data Ingestion", icon: "upload", roles: ["analyst", "admin"] },
  { href: "/monitoring", label: "Model Monitoring", icon: "activity", roles: ["analyst", "admin"] },
  { href: "/admin", label: "Admin", icon: "shield", roles: ["admin"] },
];

export function navFor(role: Role): NavItem[] {
  return NAV.filter((n) => !n.roles || role === "admin" || n.roles.includes(role));
}

export const ROLE_LABEL: Record<Role, string> = {
  commissioner: "Commissioner",
  department_head: "Department head",
  ward_officer: "Ward officer",
  analyst: "Analyst",
  admin: "Administrator",
};
