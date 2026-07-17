import { HexSignalRing } from "@/components/HexSignalRing";

export function ViewerMapSkeleton() {
  return (
    <div
      className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[#07111f] pt-14"
      role="status"
      aria-live="polite"
      aria-label="Loading viewer"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 pt-14"
        style={{
          backgroundImage: `
            radial-gradient(900px 520px at 50% 18%, rgba(34,211,238,0.05), transparent 62%),
            radial-gradient(700px 480px at 18% 82%, rgba(37,99,235,0.06), transparent 65%),
            linear-gradient(180deg, rgba(7,17,31,1), rgba(8,18,34,1))
          `,
        }}
      />

      <div className="relative flex flex-1 items-center justify-center">
        <div className="glass-overlay flex min-w-36 flex-col items-center gap-3 rounded-2xl px-5 py-4 shadow-[0_22px_64px_rgba(0,0,0,0.36)]">
          <HexSignalRing />
          <div className="max-w-[13rem] text-center text-xs font-medium text-white/76">
            Loading viewer
          </div>
        </div>
      </div>

      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-8 flex justify-center px-4"
      >
        <div className="h-12 w-[min(92vw,640px)] rounded-2xl border border-white/10 bg-[#0b1526]/72 shadow-[0_18px_52px_rgba(0,0,0,0.28)] backdrop-blur-md" />
      </div>
    </div>
  );
}
