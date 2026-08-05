export function MetricCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card card-pad">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
    </div>
  );
}
