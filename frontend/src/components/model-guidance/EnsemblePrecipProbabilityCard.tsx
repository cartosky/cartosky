// Precipitation probability thresholds — Phase 3 dependency (requires per-member
// data). Phase 2 renders the table structure with BLANK cells and a banner.
// Never render dashes or fabricated values.

const THRESHOLDS = ['P(>0.10")', 'P(>0.25")', 'P(>0.50")', 'P(>1.00")'] as const;
const WINDOWS = ["24 hr (fh 24)", "7 day (fh 168)", "15 day (fh 360)"] as const;

export function EnsemblePrecipProbabilityCard() {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 md:p-5">
      <div className="mb-3">
        <h3 className="text-[14px] font-medium text-white/85">
          Precipitation probability
        </h3>
      </div>

      <div className="mb-4 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-[12px] text-white/55">
        Coming in a future update — requires per-member ensemble data.
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="text-left text-white/40">
              <th className="py-2 pr-4 font-medium">Threshold</th>
              {WINDOWS.map((window) => (
                <th key={window} className="py-2 pr-4 font-medium">
                  {window}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {THRESHOLDS.map((threshold) => (
              <tr key={threshold} className="border-t border-white/[0.06]">
                <td className="py-2.5 pr-4 text-white/65">{threshold}</td>
                {WINDOWS.map((window) => (
                  // Intentionally blank — no fabricated values until per-member
                  // data is available (Phase 3).
                  <td key={window} className="py-2.5 pr-4" aria-label="No data" />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
