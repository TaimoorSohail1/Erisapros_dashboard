import { LoaderCircle } from "lucide-react";
import type { CSSProperties } from "react";

export function InlineLoader({ label }: { label: string }) {
  return (
    <span className="inline-loader" role="status" aria-live="polite">
      <LoaderCircle aria-hidden="true" size={16} />
      <span>{label}</span>
    </span>
  );
}

export function Skeleton({
  className = "",
  style,
}: {
  className?: string;
  style?: CSSProperties;
}) {
  return <span aria-hidden="true" className={`ui-skeleton ${className}`.trim()} style={style} />;
}

export function BrandedLoader({
  detail,
  label,
}: {
  detail?: string;
  label: string;
}) {
  return (
    <section className="branded-loader" role="status" aria-live="polite">
      <span className="branded-loader-mark">
        <LoaderCircle aria-hidden="true" size={24} />
      </span>
      <div>
        <strong>{label}</strong>
        {detail ? <small>{detail}</small> : null}
      </div>
    </section>
  );
}
