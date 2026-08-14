import { useCallback, useEffect, useState } from "react";

/**
 * Two routes do not justify a routing library. This is enough: read the path,
 * listen for back/forward, and expose a navigate that pushes history.
 *
 * Cloudflare Pages needs public/_redirects to serve index.html for every path,
 * otherwise a hard refresh on /finance 404s before this ever runs.
 */
export function useRoute() {
  const [path, setPath] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((next) => {
    if (next === window.location.pathname) return;
    window.history.pushState({}, "", next);
    setPath(next);
  }, []);

  return [path, navigate];
}

/** An anchor that routes in-app but still behaves like a link. */
export function routeLinkProps(href, navigate) {
  return {
    href,
    onClick: (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      navigate(href);
    },
  };
}
