import { Link } from "react-router-dom";

export default function SiteFooter() {
  return (
    <footer className="border-t border-white/8 bg-[#07101d]">
      <div className="mx-auto grid max-w-6xl gap-10 px-5 py-12 md:grid-cols-[1.2fr_0.8fr_0.8fr] md:px-8">
        <div>
          <div className="text-lg font-semibold tracking-tight text-white">CartoSky</div>
          <p className="mt-3 max-w-sm text-sm leading-7 text-white/56">
            Serious weather guidance in a cleaner map-first interface for model switching, forecast
            timing, and seasonal analysis.
          </p>
        </div>

        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/38">Explore</div>
          <div className="mt-4 flex flex-col gap-3 text-sm text-white/68">
            <Link to="/viewer" className="transition duration-150 hover:text-white">
              Viewer
            </Link>
            <Link to="/models" className="transition duration-150 hover:text-white">
              Models
            </Link>
            <Link to="/variables" className="transition duration-150 hover:text-white">
              Variables
            </Link>
            <Link to="/login" className="transition duration-150 hover:text-white">
              Login
            </Link>
          </div>
        </div>

        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/38">Interface</div>
          <div className="mt-4 space-y-3 text-sm text-white/56">
            <p>Map-first viewer workflow</p>
            <p>Freshness-aware model guidance</p>
            <p>Winter, severe, hydro, and upper-air products</p>
          </div>
        </div>
      </div>

      <div className="border-t border-white/6">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-5 py-5 text-[11px] uppercase tracking-[0.18em] text-white/32 md:flex-row md:items-center md:justify-between md:px-8">
          <span>© {new Date().getFullYear()} CartoSky</span>
          <span>Model data, clearly rendered.</span>
        </div>
      </div>
    </footer>
  );
}
