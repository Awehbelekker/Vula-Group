/**
 * VulaPages.jsx — Puck drag-and-drop page builder, available to EVERY tenant from the dashboard.
 * Lists a tenant's pages, creates new ones, and edits them with the shared Puck config. Saves to
 * the tenant-scoped pages API. Published pages render at /pages/:tenant/:slug (public route) and,
 * for storefronts that ship the matching renderer (e.g. OTH), at the storefront's /p/:slug too.
 *
 * Editor depth (2026-07-17): draft-vs-publish, SEO fields, rename, duplicate, starter templates,
 * and live product blocks (window.__VULA_PAGE_TENANT injected for them).
 */
import "@measured/puck/puck.css";
import PagesV2 from "./pages/PagesV2";

export default function VulaPages({ ...props }) {
  return <PagesV2 {...props} />;
}
