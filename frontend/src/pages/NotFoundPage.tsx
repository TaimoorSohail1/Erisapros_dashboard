import { ArrowLeft, SearchX } from "lucide-react";
import { Link } from "../router";

export function NotFoundPage() {
  return (
    <section className="not-found-page card" aria-labelledby="not-found-title">
      <span><SearchX size={30} /></span>
      <p className="eyebrow">Page not found</p>
      <h1 id="not-found-title">This ERISAPros page does not exist.</h1>
      <p>Check the address, or return to the dashboard to continue reviewing filings.</p>
      <Link className="button" to="/"><ArrowLeft size={17} /> Return to dashboard</Link>
    </section>
  );
}
