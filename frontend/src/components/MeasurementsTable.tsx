import type { Feature, FeatureList, Measurement, MeasurementList } from "../api";
import { featureName, formatNumber } from "../format";
import { StatusBadge } from "./StatusBadge";

interface Props {
  measurements: MeasurementList;
  features: FeatureList;
}

const MEASUREMENT_LABELS: Record<Measurement["measurement_type"], string> = {
  AREA: "Area",
  LENGTH: "Length",
  NONE: "Not measured",
};

// Every empty cell shows the same muted dash.
const EMPTY = <span className="muted">—</span>;

export function MeasurementsTable({ measurements, features }: Props) {
  // Feature names and layers come from GET /features; the rows themselves from GET /measurements.
  const featuresById = new Map(features.items.map((feature) => [feature.id, feature]));
  const { summary } = measurements;
  // Only the categories that occur are coloured, so the summary reads at a glance.
  const counts = [
    ["Calculated", summary.calculated, "success"],
    ["Not applicable", summary.not_applicable, "neutral"],
    ["Unsupported", summary.unsupported, "warning"],
    ["Failed", summary.failed, "danger"],
  ] as const;

  return (
    <section className="card">
      <div className="card-header">
        <h2>3. Measurements</h2>
        <code className="source">GET /api/v1/files/{"{id}"}/measurements</code>
      </div>

      <ul className="pills" aria-label="Measurement summary">
        {counts.map(([label, count, tone]) => (
          <li key={label} className={`pill pill-${count > 0 ? tone : "neutral"}`}>
            {label} <strong>{count}</strong>
          </li>
        ))}
      </ul>

      {measurements.items.length === 0 ? (
        <p className="muted">This file has no features to measure.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Feature</th>
                <th>Geometry</th>
                <th>Measurement</th>
                <th className="num">Value</th>
                <th>Unit</th>
                <th>Measured in</th>
                <th>Status</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {measurements.items.map((measurement) => {
                const feature = featuresById.get(measurement.feature_id);
                const name = featureName(feature);
                const notMeasured = measurement.measurement_type === "NONE";
                return (
                  <tr key={`${measurement.feature_id}-${measurement.measurement_type}`}>
                    <td className="mono muted">{measurement.feature_index}</td>
                    <td>
                      <div>{name ?? feature?.layer ?? EMPTY}</div>
                      {name && feature && <div className="muted small">{feature.layer}</div>}
                    </td>
                    <td>{measurement.geometry_type ?? EMPTY}</td>
                    <td className={notMeasured ? "muted" : undefined}>
                      {MEASUREMENT_LABELS[measurement.measurement_type]}
                    </td>
                    <td className="num">
                      {measurement.value === null ? EMPTY : formatNumber(measurement.value)}
                    </td>
                    <td>{measurement.value !== null && measurement.unit ? measurement.unit : EMPTY}</td>
                    <td className={measurement.measurement_crs ? "mono" : undefined}>
                      {measurement.measurement_crs ?? EMPTY}
                    </td>
                    <td>
                      <StatusBadge status={measurement.status} />
                    </td>
                    <td className="details">{reason(measurement, feature)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {measurements.total > measurements.items.length && (
        <p className="muted small">
          Showing the first {measurements.items.length} of {measurements.total} measurements.
        </p>
      )}
    </section>
  );
}

// The API's own message when it gives one. Points come back without a message, so say why here.
function reason(measurement: Measurement, feature: Feature | undefined): string | null {
  if (measurement.error_message) return measurement.error_message;
  if (feature?.geometry_error) return feature.geometry_error;
  if (measurement.status === "NOT_APPLICABLE") {
    return "Area and length do not apply to this geometry type.";
  }
  return null;
}
