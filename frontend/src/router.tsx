import type { AnchorHTMLAttributes, ReactNode } from "react";

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  to: string;
  children: ReactNode;
};

export function Link({ to, children, ...props }: LinkProps) {
  return <a href={to} {...props}>{children}</a>;
}

export function NavLink({ to, end = false, className, children, ...props }: LinkProps & { end?: boolean }) {
  const path = window.location.pathname;
  const active = end ? path === to : path === to || path.startsWith(to + "/");
  const classes = [className, active ? "active" : ""].filter(Boolean).join(" ");
  return <a href={to} className={classes || undefined} {...props}>{children}</a>;
}

export function useParams<T extends Record<string, string | undefined>>(): T {
  const filingMatch = window.location.pathname.match(/^\/filings\/([^/]+)\/?$/);
  return { id: filingMatch ? decodeURIComponent(filingMatch[1]) : undefined } as unknown as T;
}
